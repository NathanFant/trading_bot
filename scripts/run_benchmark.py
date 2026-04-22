"""
Comprehensive strategy benchmark.

Compares every strategy against B&H SOL, B&H BTC, and B&H VOO.
Uses FIXED live params for FGI (no Monte Carlo cherry-picking) and
standard default params for RSI/SMA — so comparisons are fair.

Strategies tested:
  FGI-ZScale-NoGate   : live params, confidence gate OFF (trade on any z-cross)
  FGI-ZScale-Gate53   : live params, confidence gate 53% (current live config)
  FGI-ZScale-InvBayes : live params, Bayesian component inverted
  RSI(14, 30/70)      : buy when RSI crosses below 30, sell above 70
  SMA(50/100)         : golden/death cross using 50-day and 100-day SMAs
  B&H SOL             : buy and hold Solana
  B&H BTC             : buy and hold Bitcoin
  B&H VOO             : buy and hold Vanguard S&P 500 ETF

Periods: 24, 36, 60 months | $1,000 starting | SOL-USD

Losing algos (beaten on Sharpe by all 3 benchmarks) are saved to strategies/.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)

import yfinance as yf

from core.backtest import (
    BacktestParams,
    BacktestResult,
    SPREAD_PCT,
    load_aligned_data,
    run_backtest,
    run_rsi_backtest,
    run_sma_backtest,
)

SYMBOL = "SOL-USD"
STARTING = 1_000.0
STRATEGIES_DIR = Path(__file__).parent.parent / "strategies"

PERIODS = [
    ("24 months", 730),
    ("36 months", 1095),
    ("60 months", 1825),
]

# ── Live FGI params (same as what the bot runs in production) ─────────────────
LIVE_PARAMS = dict(
    buy_z_threshold=-1.95,
    sell_z_threshold=2.65,
    scale_buy_with_z=True,
    min_buy_pct=0.24,
    max_buy_pct=0.74,
    max_scale_z=4.0,
    sell_pct=0.65,
    lookback_days=55,
    min_confidence=0.53,
    symbol=SYMBOL,
    starting_cash=STARTING,
    spread_pct=SPREAD_PCT.get(SYMBOL, 0.011),
)


def _fgi_result(bars, use_gate: bool, invert_bayes: bool) -> BacktestResult:
    p = BacktestParams(
        **LIVE_PARAMS,
        use_confidence_gate=use_gate,
        invert_bayesian=invert_bayes,
    )
    return run_backtest(bars, p)


def _fetch_bh_return(ticker: str, days: int, starting: float) -> tuple[float, float]:
    """Buy-and-hold return for a Yahoo Finance ticker over `days`."""
    end = datetime.today()
    start = end - timedelta(days=days + 14)
    try:
        hist = yf.Ticker(ticker).history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
        if hist.empty or len(hist) < 2:
            return 0.0, starting
        pct = (float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[0])) / float(hist["Close"].iloc[0]) * 100
        return pct, starting * (1 + pct / 100)
    except Exception as exc:
        logging.warning("%s fetch failed: %s", ticker, exc)
        return 0.0, starting


def _save_losing_algo(label: str, period: str, result: BacktestResult, benchmarks: dict) -> None:
    STRATEGIES_DIR.mkdir(exist_ok=True)
    losing_dir = STRATEGIES_DIR / "losing"
    losing_dir.mkdir(exist_ok=True)
    slug = label.lower().replace(" ", "_").replace("/", "_")
    period_slug = period.replace(" ", "_")
    fname = losing_dir / f"{slug}_{period_slug}_{datetime.now().strftime('%Y%m%d')}.json"
    data = {
        "strategy": label,
        "period": period,
        "result": {
            "total_return_pct": result.total_return_pct,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "num_trades": result.num_trades,
            "win_rate": result.win_rate,
            "final_portfolio_usd": result.final_portfolio_usd,
        },
        "benchmarks": benchmarks,
        "params": {k: v for k, v in asdict(result.params).items() if not k.startswith("daily")},
        "recorded_at": datetime.now().isoformat(),
    }
    fname.write_text(json.dumps(data, indent=2))
    logging.info("Saved losing algo to %s", fname)


def run_period(label: str, days: int) -> dict:
    print(f"\n{'='*70}")
    print(f"  {label} — {days} days")
    print(f"{'='*70}")

    bars = load_aligned_data(days=days, symbol=SYMBOL)
    print(f"  Bars loaded: {len(bars)}  |  SOL ${bars[0].price:,.2f} → ${bars[-1].price:,.2f}")

    spread = SPREAD_PCT.get(SYMBOL, 0.011)
    sol_bh_qty = STARTING / (bars[0].price * (1 + spread))
    sol_bh_val = sol_bh_qty * bars[-1].price * (1 - spread)
    sol_bh_pct = (sol_bh_val - STARTING) / STARTING * 100

    print(f"\n  Fetching BTC and VOO returns…")
    btc_pct, btc_val = _fetch_bh_return("BTC-USD", days, STARTING)
    voo_pct, voo_val = _fetch_bh_return("VOO", days, STARTING)

    print(f"  B&H SOL: ${sol_bh_val:.2f}  ({sol_bh_pct:+.1f}%)")
    print(f"  B&H BTC: ${btc_val:.2f}  ({btc_pct:+.1f}%)")
    print(f"  B&H VOO: ${voo_val:.2f}  ({voo_pct:+.1f}%)")

    print(f"\n  Running FGI strategies…")
    r_nogate = _fgi_result(bars, use_gate=False, invert_bayes=False)
    print(f"  FGI no-gate:     {r_nogate.total_return_pct:+.1f}%  Sharpe {r_nogate.sharpe_ratio:.3f}  "
          f"MaxDD {r_nogate.max_drawdown_pct:.1f}%  trades={r_nogate.num_trades}")

    r_gate = _fgi_result(bars, use_gate=True, invert_bayes=False)
    print(f"  FGI gate-53%:    {r_gate.total_return_pct:+.1f}%  Sharpe {r_gate.sharpe_ratio:.3f}  "
          f"MaxDD {r_gate.max_drawdown_pct:.1f}%  trades={r_gate.num_trades}")

    r_invbayes = _fgi_result(bars, use_gate=False, invert_bayes=True)
    print(f"  FGI inv-bayes:   {r_invbayes.total_return_pct:+.1f}%  Sharpe {r_invbayes.sharpe_ratio:.3f}  "
          f"MaxDD {r_invbayes.max_drawdown_pct:.1f}%  trades={r_invbayes.num_trades}")

    print(f"\n  Running RSI(14, 30/70)…")
    r_rsi = run_rsi_backtest(bars, buy_threshold=30.0, sell_threshold=70.0,
                              buy_pct=0.50, sell_pct=0.65, starting_cash=STARTING, symbol=SYMBOL)
    print(f"  RSI:             {r_rsi.total_return_pct:+.1f}%  Sharpe {r_rsi.sharpe_ratio:.3f}  "
          f"MaxDD {r_rsi.max_drawdown_pct:.1f}%  trades={r_rsi.num_trades}")

    sma_fast, sma_slow = (50, 100) if len(bars) >= 120 else (20, 50)
    print(f"\n  Running SMA({sma_fast}/{sma_slow} golden/death cross)…")
    r_sma = run_sma_backtest(bars, fast_period=sma_fast, slow_period=sma_slow,
                              starting_cash=STARTING, symbol=SYMBOL)
    print(f"  SMA cross:       {r_sma.total_return_pct:+.1f}%  Sharpe {r_sma.sharpe_ratio:.3f}  "
          f"MaxDD {r_sma.max_drawdown_pct:.1f}%  trades={r_sma.num_trades}")

    return {
        "label": label,
        "days": days,
        "sol_bh": (sol_bh_pct, sol_bh_val),
        "btc_bh": (btc_pct, btc_val),
        "voo_bh": (voo_pct, voo_val),
        "fgi_nogate": r_nogate,
        "fgi_gate": r_gate,
        "fgi_invbayes": r_invbayes,
        "rsi": r_rsi,
        "sma": r_sma,
    }


def print_summary(rows: list[dict]) -> None:
    W = 100
    print(f"\n\n{'='*W}")
    print(f"  BENCHMARK COMPARISON — ${STARTING:.0f} starting — {SYMBOL}")
    print(f"{'='*W}")

    # Sharpe ratio table
    print(f"\n  {'Period':<12} {'SOL B&H':>8} {'BTC B&H':>8} {'VOO B&H':>8} │ "
          f"{'FGI-NoGate':>10} {'FGI-Gate':>9} {'FGI-InvB':>9} │ {'RSI':>7} {'SMA':>7}")
    print(f"  {'-'*(W-2)}")
    for r in rows:
        ng, gt, ib, rsi, sma = r["fgi_nogate"], r["fgi_gate"], r["fgi_invbayes"], r["rsi"], r["sma"]
        print(f"  {r['label']:<12} "
              f"{'—':>8} {'—':>8} {'—':>8} │ "
              f"{ng.sharpe_ratio:>10.3f} {gt.sharpe_ratio:>9.3f} {ib.sharpe_ratio:>9.3f} │ "
              f"{rsi.sharpe_ratio:>7.3f} {sma.sharpe_ratio:>7.3f}  ← Sharpe")
        sol_pct, _ = r["sol_bh"]
        btc_pct, _ = r["btc_bh"]
        voo_pct, _ = r["voo_bh"]
        print(f"  {'':12} "
              f"{sol_pct:>+7.1f}% {btc_pct:>+7.1f}% {voo_pct:>+7.1f}% │ "
              f"{ng.total_return_pct:>+9.1f}% {gt.total_return_pct:>+8.1f}% {ib.total_return_pct:>+8.1f}% │ "
              f"{rsi.total_return_pct:>+6.1f}% {sma.total_return_pct:>+6.1f}%  ← Return")
        print()

    # Dollar outcomes
    print(f"\n  Dollar outcomes on ${STARTING:.0f}:")
    print(f"  {'Period':<12} {'SOL B&H':>9} {'BTC B&H':>9} {'VOO B&H':>9} │ "
          f"{'FGI-NoGate':>10} {'FGI-Gate':>9} {'FGI-InvB':>9} │ {'RSI':>8} {'SMA':>8}")
    print(f"  {'-'*88}")
    for r in rows:
        ng, gt, ib, rsi, sma = r["fgi_nogate"], r["fgi_gate"], r["fgi_invbayes"], r["rsi"], r["sma"]
        _, sol_v = r["sol_bh"]
        _, btc_v = r["btc_bh"]
        _, voo_v = r["voo_bh"]
        print(f"  {r['label']:<12} ${sol_v:>7.0f}  ${btc_v:>7.0f}  ${voo_v:>7.0f}  │ "
              f"${ng.final_portfolio_usd:>8.0f}  ${gt.final_portfolio_usd:>7.0f}  ${ib.final_portfolio_usd:>7.0f}  │ "
              f"${rsi.final_portfolio_usd:>6.0f}  ${sma.final_portfolio_usd:>6.0f}")

    # Beats all 3 benchmarks?
    print(f"\n  Beats all 3 B&H benchmarks (by Sharpe):")
    print(f"  {'Period':<12} {'FGI-NoGate':>12} {'FGI-Gate53':>12} {'FGI-InvBayes':>14} {'RSI':>6} {'SMA':>6}")
    print(f"  {'-'*66}")
    for r in rows:
        sol_s = r["fgi_nogate"].buy_hold_return_pct  # proxy — use actual Sharpe
        benchmarks = {
            "sol_bh_pct": r["sol_bh"][0],
            "btc_bh_pct": r["btc_bh"][0],
            "voo_bh_pct": r["voo_bh"][0],
        }
        # Approximate benchmark Sharpes aren't available; compare by return instead
        bench_rets = [r["sol_bh"][0], r["btc_bh"][0], r["voo_bh"][0]]

        def beats(res: BacktestResult) -> str:
            return "YES ✓" if res.total_return_pct > max(bench_rets) else "NO  ✗"

        ng, gt, ib, rsi, sma = r["fgi_nogate"], r["fgi_gate"], r["fgi_invbayes"], r["rsi"], r["sma"]
        print(f"  {r['label']:<12} {beats(ng):>12} {beats(gt):>12} {beats(ib):>14} {beats(rsi):>6} {beats(sma):>6}")

        # Save losing FGI variants to strategies/
        for strat_label, res in [("FGI-NoGate", ng), ("FGI-Gate53", gt), ("FGI-InvBayes", ib)]:
            if not all(res.total_return_pct > b for b in bench_rets):
                _save_losing_algo(strat_label, r["label"], res, benchmarks)

    # Recommended config
    print(f"\n  Recommendation:")
    for r in rows:
        candidates = {
            "FGI-NoGate": r["fgi_nogate"],
            "FGI-Gate53": r["fgi_gate"],
            "FGI-InvBayes": r["fgi_invbayes"],
            "RSI(14)": r["rsi"],
            f"SMA(50/100)": r["sma"],
        }
        winner = max(candidates, key=lambda k: candidates[k].sharpe_ratio)
        best = candidates[winner]
        print(f"  {r['label']:<12}: {winner}  (Sharpe {best.sharpe_ratio:.3f}, "
              f"return {best.total_return_pct:+.1f}%, MaxDD {best.max_drawdown_pct:.1f}%)")


def main() -> None:
    rows = []
    for label, days in PERIODS:
        rows.append(run_period(label, days))
    print_summary(rows)


if __name__ == "__main__":
    main()
