"""
Five-way comparison: Symmetric vs Asymmetric vs Z-Scaled vs SOL B&H vs VOO B&H.

Z-Scaled — buy fraction ramps up as FGI z-score grows more extreme.
           At threshold z: buy min_buy_pct of cash.
           At max_scale_z: buy max_buy_pct of cash.
           Sell fraction is independent (same as asymmetric).

Periods: 12, 24, 36, 60 months  |  Starting capital: $100  |  SOL-USD
200 Monte Carlo simulations per strategy per period.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

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
    BacktestResult,
    SPREAD_PCT,
    load_aligned_data,
    run_monte_carlo,
    run_asymmetric_monte_carlo,
    run_scaled_monte_carlo,
)

SYMBOL = "SOL-USD"
STARTING = 100.0
N_SIMS = 200
SPREAD = SPREAD_PCT[SYMBOL]

PERIODS = [
    ("12 months",  365),
    ("24 months",  730),
    ("36 months", 1095),
    ("60 months", 1825),
]


def fetch_voo_return(days: int, starting: float) -> tuple[float, float]:
    end = datetime.today()
    start = end - timedelta(days=days + 10)
    try:
        hist = yf.Ticker("VOO").history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
        if hist.empty or len(hist) < 2:
            return 0.0, starting
        pct = (float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[0])) / float(hist["Close"].iloc[0]) * 100
        return pct, starting * (1 + pct / 100)
    except Exception as exc:
        logging.warning("VOO fetch failed: %s", exc)
        return 0.0, starting


@dataclass
class Row:
    label: str
    btc_bh_pct: float
    voo_pct: float
    voo_end: float
    sym: BacktestResult
    asym: BacktestResult
    scaled: BacktestResult


def run_period(label: str, days: int) -> Row:
    print(f"\n{'='*64}")
    print(f"  {label} — {days} days")
    print(f"{'='*64}")

    bars = load_aligned_data(days=days, symbol=SYMBOL)
    print(f"  Bars: {len(bars)}  |  SOL ${bars[0].price:,.2f} → ${bars[-1].price:,.2f}")

    bh_qty = STARTING / (bars[0].price * (1 + SPREAD))
    bh_val = bh_qty * bars[-1].price * (1 - SPREAD)
    bh_pct = (bh_val - STARTING) / STARTING * 100
    print(f"  SOL Buy & Hold: ${bh_val:.2f}  ({bh_pct:+.1f}%)")

    voo_pct, voo_end = fetch_voo_return(days, STARTING)
    print(f"  VOO Buy & Hold: ${voo_end:.2f}  ({voo_pct:+.1f}%)\n")

    print(f"  Symmetric MC ({N_SIMS} sims)…")
    sym = run_monte_carlo(bars, n_simulations=N_SIMS, symbol=SYMBOL, starting_cash=STARTING)[0]
    print(f"  → {sym.total_return_pct:+.1f}%  Sharpe {sym.sharpe_ratio:.3f}  MaxDD {sym.max_drawdown_pct:.1f}%"
          f"  buy%={sym.params.position_size_pct:.0%}")

    print(f"  Asymmetric MC ({N_SIMS} sims)…")
    asym = run_asymmetric_monte_carlo(bars, n_simulations=N_SIMS, symbol=SYMBOL, starting_cash=STARTING)[0]
    asym_sell = asym.params.sell_pct if asym.params.sell_pct is not None else asym.params.position_size_pct
    print(f"  → {asym.total_return_pct:+.1f}%  Sharpe {asym.sharpe_ratio:.3f}  MaxDD {asym.max_drawdown_pct:.1f}%"
          f"  buy%={asym.params.position_size_pct:.0%} sell%={asym_sell:.0%}")

    print(f"  Z-Scaled MC ({N_SIMS} sims)…")
    scaled = run_scaled_monte_carlo(bars, n_simulations=N_SIMS, symbol=SYMBOL, starting_cash=STARTING)[0]
    sc_sell = scaled.params.sell_pct if scaled.params.sell_pct is not None else scaled.params.position_size_pct
    print(f"  → {scaled.total_return_pct:+.1f}%  Sharpe {scaled.sharpe_ratio:.3f}  MaxDD {scaled.max_drawdown_pct:.1f}%"
          f"  min_buy%={scaled.params.min_buy_pct:.0%} max_buy%={scaled.params.max_buy_pct:.0%} sell%={sc_sell:.0%}")

    return Row(label=label, btc_bh_pct=bh_pct, voo_pct=voo_pct, voo_end=voo_end,
               sym=sym, asym=asym, scaled=scaled)


def print_summary(rows: list[Row]) -> None:
    W = 100
    print(f"\n\n{'='*W}")
    print(f"  FIVE-WAY COMPARISON — ${STARTING:.0f} starting — {SYMBOL}")
    print(f"{'='*W}")

    # Returns + risk table
    print(f"\n  {'Period':<12} {'SOL B&H':>8} {'VOO B&H':>8} │ "
          f"{'Sym':>7} {'Shr':>6} │ "
          f"{'Asym':>7} {'Shr':>6} │ "
          f"{'Scaled':>7} {'Shr':>6}")
    print(f"  {'-'*(W-2)}")
    for r in rows:
        print(
            f"  {r.label:<12} {r.btc_bh_pct:>+7.1f}% {r.voo_pct:>+7.1f}% │"
            f" {r.sym.total_return_pct:>+6.1f}% {r.sym.sharpe_ratio:>6.3f} │"
            f" {r.asym.total_return_pct:>+6.1f}% {r.asym.sharpe_ratio:>6.3f} │"
            f" {r.scaled.total_return_pct:>+6.1f}% {r.scaled.sharpe_ratio:>6.3f}"
        )

    # Dollar outcomes
    print(f"\n  Dollar outcomes on ${STARTING:.0f}:")
    print(f"  {'Period':<12} {'SOL B&H':>9} {'VOO B&H':>9} {'Sym':>9} {'Asym':>9} {'Scaled':>9}  {'Best Strategy':>14}")
    print(f"  {'-'*78}")
    for r in rows:
        btc_end = STARTING * (1 + r.btc_bh_pct / 100)  # reused var name, now SOL B&H
        ends = {"Symmetric": r.sym, "Asymmetric": r.asym, "Z-Scaled": r.scaled}
        best_name = max(ends, key=lambda k: ends[k].sharpe_ratio)
        print(f"  {r.label:<12} ${btc_end:>7.2f}  ${r.voo_end:>7.2f}"
              f"  ${r.sym.final_portfolio_usd:>7.2f}  ${r.asym.final_portfolio_usd:>7.2f}"
              f"  ${r.scaled.final_portfolio_usd:>7.2f}   {best_name:>14}")

    # Beats benchmarks
    print(f"\n  Beats benchmarks (by return):")
    print(f"  {'Period':<12} {'Sym>SOL':>8} {'Sym>VOO':>8} {'Asym>SOL':>9} {'Asym>VOO':>9} {'Scl>SOL':>8} {'Scl>VOO':>8}")
    print(f"  {'-'*68}")
    for r in rows:
        def yn(cond: bool) -> str: return "YES ✓" if cond else "NO  ✗"
        print(f"  {r.label:<12}"
              f" {yn(r.sym.total_return_pct > r.btc_bh_pct):>8}"
              f" {yn(r.sym.total_return_pct > r.voo_pct):>8}"
              f" {yn(r.asym.total_return_pct > r.btc_bh_pct):>9}"
              f" {yn(r.asym.total_return_pct > r.voo_pct):>9}"
              f" {yn(r.scaled.total_return_pct > r.btc_bh_pct):>8}"
              f" {yn(r.scaled.total_return_pct > r.voo_pct):>8}")

    # Optimal params
    print(f"\n  Optimal parameters:")
    print(f"  {'Period':<12} {'Type':<12} {'buy_z':>7} {'sell_z':>7} "
          f"{'min_b%':>7} {'max_b%':>7} {'sell%':>6} {'lkbk':>6} {'conf':>6}")
    print(f"  {'-'*80}")
    for r in rows:
        sp  = r.sym.params
        ap  = r.asym.params
        sc  = r.scaled.params
        a_sell  = ap.sell_pct  if ap.sell_pct  is not None else ap.position_size_pct
        sc_sell = sc.sell_pct  if sc.sell_pct  is not None else sc.position_size_pct
        print(f"  {r.label:<12} {'Symmetric':<12} {sp.buy_z_threshold:>7.2f} {sp.sell_z_threshold:>7.2f}"
              f" {sp.position_size_pct:>6.0%}    n/a  {sp.position_size_pct:>5.0%} {sp.lookback_days:>5}d {sp.min_confidence:>5.2f}")
        print(f"  {'':12} {'Asymmetric':<12} {ap.buy_z_threshold:>7.2f} {ap.sell_z_threshold:>7.2f}"
              f" {ap.position_size_pct:>6.0%}    n/a  {a_sell:>5.0%} {ap.lookback_days:>5}d {ap.min_confidence:>5.2f}")
        print(f"  {'':12} {'Z-Scaled':<12} {sc.buy_z_threshold:>7.2f} {sc.sell_z_threshold:>7.2f}"
              f" {sc.min_buy_pct:>6.0%} {sc.max_buy_pct:>6.0%}  {sc_sell:>5.0%} {sc.lookback_days:>5}d {sc.min_confidence:>5.2f}")
        print()


def main() -> None:
    rows: list[Row] = []
    for label, days in PERIODS:
        rows.append(run_period(label, days))
    print_summary(rows)


if __name__ == "__main__":
    main()
