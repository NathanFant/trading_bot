"""
Micro-trading strategy backtester — Iteration 2.

Focuses on 30m and 1h timeframes (5m/10m/15m killed by NFA fee vs. move size).
Includes parameter sweep on best strategies.

Strategies:
  EMA-ADX           : EMA cross + ADX trend strength gate
  EMA-ADX-Regime    : EMA-ADX + 6h regime filter (only trade in macro direction)
  Trend-Pullback    : EMA(100) trend + RSI pullback entry (classic high-WR approach)
  BB-Squeeze        : BB compression breakout
  Keltner-Breakout  : Close outside Keltner Channel, confirmed by EMA(50)
  MACD-Cross        : MACD histogram zero-cross + EMA trend

Starting: $1,000 | 5x leverage | 35% margin fraction | SOL perp
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.micro_backtest import (
    StrategyResult, Trade,
    load_candles, run_micro_backtest,
    strat_ema_adx, strat_ema_adx_regime, strat_ema_adx_regime_v2,
    strat_trend_pullback, strat_bb_squeeze, strat_keltner_breakout, strat_macd_cross,
)

ASSET   = "SOL"
START   = 1_000.0
W       = 110


def _star(r: StrategyResult) -> str:
    if r.total_return_pct > 20 and r.sharpe > 1.0 and r.max_drawdown_pct < 30:
        return " ★★★"
    if r.total_return_pct > 10 and r.sharpe > 0.5:
        return " ★★ "
    if r.total_return_pct > 0:
        return " ★  "
    return "    "


def row(r: StrategyResult, extra: str = "") -> str:
    return (f"  {r.name:<22} {r.timeframe:<5} {r.total_return_pct:>+8.1f}%  "
            f"Sharpe {r.sharpe:>6.2f}  MaxDD {r.max_drawdown_pct:>5.1f}%  "
            f"N={r.num_trades:>3}  WR {r.win_rate:>5.1f}%  "
            f"PF {r.profit_factor:>5.2f}  ${r.final_usd:>8.2f}"
            f"{_star(r)}{extra}")


def run_all() -> list[StrategyResult]:
    results: list[StrategyResult] = []

    # Load data
    tfs = {}
    for tf in ("30m", "1h", "6h"):
        try:
            tfs[tf] = load_candles(ASSET, tf)
        except FileNotFoundError as e:
            print(f"  WARNING: {e}")

    if not tfs:
        print("No data files found — run scripts/fetch_perp_data.py first.")
        return []

    p0 = tfs["30m"]["close"].iloc[0]
    p1 = tfs["30m"]["close"].iloc[-1]
    bh = (p1 / p0 - 1) * 100
    print(f"  SOL B&H: {bh:+.1f}%  (${p0:.2f} → ${p1:.2f})\n")
    print(f"  {'Strategy':<22} {'TF':<5} {'Return':>9}  Sharpe    MaxDD    N    WR      PF       Final")
    print(f"  {'-'*(W-2)}")

    # ── Core strategies on 30m and 1h ─────────────────────────────────────────
    strategies = [
        ("EMA-ADX",        lambda df: strat_ema_adx(df, adx_min=20, sl_mult=1.5, tp_mult=4.0)),
        ("EMA-ADX(adx18)", lambda df: strat_ema_adx(df, adx_min=18, sl_mult=1.5, tp_mult=4.0)),
        ("EMA-ADX(adx15)", lambda df: strat_ema_adx(df, adx_min=15, sl_mult=1.5, tp_mult=4.0)),
        ("EMA-ADX(tp3.5)", lambda df: strat_ema_adx(df, adx_min=18, sl_mult=1.5, tp_mult=3.5)),
        ("EMA-ADX(tp5.0)", lambda df: strat_ema_adx(df, adx_min=18, sl_mult=1.5, tp_mult=5.0)),
        ("EMA-ADX(sl1.0)", lambda df: strat_ema_adx(df, adx_min=18, sl_mult=1.0, tp_mult=3.0)),
    ]

    print()
    for name, fn in strategies:
        for tf in ("30m", "1h"):
            if tf not in tfs:
                continue
            sigs, atr_arr, sl_m, tp_m = fn(tfs[tf])
            r = run_micro_backtest(tfs[tf], name, tf, sigs, atr_arr, sl_m, tp_m, START)
            results.append(r)
            print(row(r))
        print()

    # ── Regime-filtered EMA-ADX (V1 and V2) ──────────────────────────────────
    if "6h" in tfs:
        df_6h = tfs["6h"]
        for tf in ("30m", "1h"):
            if tf not in tfs:
                continue
            sigs, atr_arr, sl_m, tp_m = strat_ema_adx_regime(tfs[tf], df_6h, adx_min=18)
            r = run_micro_backtest(tfs[tf], "EMA-ADX+Regime", tf, sigs, atr_arr, sl_m, tp_m, START)
            results.append(r)
            print(row(r))

        print()
        for tf in ("30m", "1h"):
            if tf not in tfs:
                continue
            sigs, atr_arr, sl_m, tp_m = strat_ema_adx_regime_v2(tfs[tf], df_6h)
            r = run_micro_backtest(tfs[tf], "EMA-ADX+Regime-V2", tf, sigs, atr_arr, sl_m, tp_m, START)
            results.append(r)
            print(row(r, "  ← V2"))
        print()

    # ── Trend Pullback ─────────────────────────────────────────────────────────
    for tf in ("30m", "1h"):
        if tf not in tfs:
            continue
        sigs, atr_arr, sl_m, tp_m = strat_trend_pullback(tfs[tf])
        r = run_micro_backtest(tfs[tf], "TrendPullback", tf, sigs, atr_arr, sl_m, tp_m, START)
        results.append(r)
        print(row(r))
    print()

    # ── BB-Squeeze ─────────────────────────────────────────────────────────────
    for tf in ("30m", "1h"):
        if tf not in tfs:
            continue
        sigs, atr_arr, sl_m, tp_m = strat_bb_squeeze(tfs[tf])
        r = run_micro_backtest(tfs[tf], "BB-Squeeze", tf, sigs, atr_arr, sl_m, tp_m, START)
        results.append(r)
        print(row(r))
    print()

    # ── Keltner Breakout ───────────────────────────────────────────────────────
    for tf in ("30m", "1h"):
        if tf not in tfs:
            continue
        sigs, atr_arr, sl_m, tp_m = strat_keltner_breakout(tfs[tf])
        r = run_micro_backtest(tfs[tf], "Keltner-Break", tf, sigs, atr_arr, sl_m, tp_m, START)
        results.append(r)
        print(row(r))
    print()

    # ── MACD-Cross ─────────────────────────────────────────────────────────────
    for tf in ("30m", "1h"):
        if tf not in tfs:
            continue
        sigs, atr_arr, sl_m, tp_m = strat_macd_cross(tfs[tf])
        r = run_micro_backtest(tfs[tf], "MACD-Cross", tf, sigs, atr_arr, sl_m, tp_m, START)
        results.append(r)
        print(row(r))

    return results


def print_summary(results: list[StrategyResult]) -> None:
    print(f"\n\n{'='*W}")
    print(f"  SUMMARY — ranked by Sharpe (looking for: return>20%, Sharpe>1.0, MaxDD<30%)")
    print(f"{'='*W}")

    ranked = sorted(results, key=lambda r: r.sharpe, reverse=True)
    for r in ranked:
        print(row(r))

    # Detailed breakdown of top 3
    top3 = [r for r in ranked if r.total_return_pct > 0][:3]
    for best in top3:
        by_exit = {}
        by_dir  = {"LONG": 0, "SHORT": 0}
        pnl_dir = {"LONG": 0.0, "SHORT": 0.0}
        for t in best.trades:
            by_exit[t.exit_reason] = by_exit.get(t.exit_reason, 0) + 1
            by_dir[t.direction]  += 1
            pnl_dir[t.direction] += t.net_pnl

        print(f"\n  ── {best.name} {best.timeframe} detail ──")
        print(f"    Return: {best.total_return_pct:+.1f}%  Sharpe: {best.sharpe:.2f}  "
              f"MaxDD: {best.max_drawdown_pct:.1f}%  Trades: {best.num_trades}")
        print(f"    WR: {best.win_rate:.1f}%  PF: {best.profit_factor:.2f}  "
              f"Avg PnL: ${best.avg_trade_pnl:.2f}  Total fees: ${best.total_fees:.2f}")
        print(f"    Exits → {by_exit}")
        print(f"    Long  {by_dir['LONG']:3d} trades  net ${pnl_dir['LONG']:+.2f}")
        print(f"    Short {by_dir['SHORT']:3d} trades  net ${pnl_dir['SHORT']:+.2f}")


def main() -> None:
    print(f"\n{'='*W}")
    print(f"  MICRO BACKTEST (Iter 2) — {ASSET} Perp — ${START:.0f} — 5x lev — 30m/1h focus")
    print(f"  Fees: 0.1% taker + $0.15 NFA/contract/side | 1-bar delay (no look-ahead)")
    print(f"{'='*W}\n")
    results = run_all()
    print_summary(results)


if __name__ == "__main__":
    main()
