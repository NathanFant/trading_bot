"""
Sell sizing comparison: fixed 65% vs z-scaled sell.

Fixed sell: always sell 65% of holdings per signal (current live config).
Z-scaled sell: sell fraction ramps from min_sell_pct → max_sell_pct as
               greed z-score intensifies. Both use z-scaled buys.

Periods: 12, 24, 36, 60 months | $100 starting | SOL-USD | 200 sims each
"""

from __future__ import annotations

import logging
import os
import sys

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

from core.backtest import (
    BacktestResult,
    SPREAD_PCT,
    load_aligned_data,
    run_scaled_monte_carlo,
    run_scaled_sell_monte_carlo,
)

SYMBOL = "SOL-USD"
STARTING = 100.0
N_SIMS = 200

PERIODS = [
    ("12 months",  365),
    ("24 months",  730),
    ("36 months", 1095),
    ("60 months", 1825),
]


def run_period(label: str, days: int) -> tuple[BacktestResult, BacktestResult]:
    print(f"\n{'='*64}")
    print(f"  {label} — {days} days")
    print(f"{'='*64}")

    bars = load_aligned_data(days=days, symbol=SYMBOL)
    print(f"  Bars: {len(bars)}  |  SOL ${bars[0].price:,.2f} → ${bars[-1].price:,.2f}")

    spread = SPREAD_PCT[SYMBOL]
    bh_qty = STARTING / (bars[0].price * (1 + spread))
    bh_val = bh_qty * bars[-1].price * (1 - spread)
    bh_pct = (bh_val - STARTING) / STARTING * 100
    print(f"  SOL Buy & Hold: ${bh_val:.2f}  ({bh_pct:+.1f}%)\n")

    print(f"  Fixed sell MC ({N_SIMS} sims)…")
    fixed = run_scaled_monte_carlo(bars, n_simulations=N_SIMS, symbol=SYMBOL, starting_cash=STARTING)[0]
    sc_sell_fixed = fixed.params.sell_pct if fixed.params.sell_pct is not None else fixed.params.position_size_pct
    print(f"  → {fixed.total_return_pct:+.1f}%  Sharpe {fixed.sharpe_ratio:.3f}  "
          f"MaxDD {fixed.max_drawdown_pct:.1f}%  "
          f"min_buy={fixed.params.min_buy_pct:.0%} max_buy={fixed.params.max_buy_pct:.0%} "
          f"sell={sc_sell_fixed:.0%} (fixed)")

    print(f"  Z-scaled sell MC ({N_SIMS} sims)…")
    scaled = run_scaled_sell_monte_carlo(bars, n_simulations=N_SIMS, symbol=SYMBOL, starting_cash=STARTING)[0]
    print(f"  → {scaled.total_return_pct:+.1f}%  Sharpe {scaled.sharpe_ratio:.3f}  "
          f"MaxDD {scaled.max_drawdown_pct:.1f}%  "
          f"min_buy={scaled.params.min_buy_pct:.0%} max_buy={scaled.params.max_buy_pct:.0%} "
          f"min_sell={scaled.params.min_sell_pct:.0%} max_sell={scaled.params.max_sell_pct:.0%}")

    return fixed, scaled


def main() -> None:
    rows = []
    for label, days in PERIODS:
        fixed, scaled = run_period(label, days)
        rows.append((label, fixed, scaled))

    W = 90
    print(f"\n\n{'='*W}")
    print(f"  SELL SCALING COMPARISON — ${STARTING:.0f} starting — {SYMBOL}")
    print(f"{'='*W}")

    print(f"\n  {'Period':<12} {'Fixed sell':>10} {'Shr':>6} {'Scaled sell':>11} {'Shr':>6}  {'Winner':>12}")
    print(f"  {'-'*(W-2)}")
    for label, fixed, scaled in rows:
        winner = "Z-Scaled" if scaled.sharpe_ratio > fixed.sharpe_ratio else "Fixed"
        print(f"  {label:<12} {fixed.total_return_pct:>+9.1f}% {fixed.sharpe_ratio:>6.3f} "
              f"{scaled.total_return_pct:>+10.1f}% {scaled.sharpe_ratio:>6.3f}  {winner:>12}")

    print(f"\n  Dollar outcomes on ${STARTING:.0f}:")
    print(f"  {'Period':<12} {'Fixed':>9} {'Z-Scaled':>9}  {'Δ':>8}")
    print(f"  {'-'*44}")
    for label, fixed, scaled in rows:
        delta = scaled.final_portfolio_usd - fixed.final_portfolio_usd
        print(f"  {label:<12} ${fixed.final_portfolio_usd:>7.2f}  ${scaled.final_portfolio_usd:>7.2f}  "
              f"{delta:>+7.2f}")

    print(f"\n  Optimal z-scaled sell parameters:")
    print(f"  {'Period':<12} {'min_sell%':>9} {'max_sell%':>9} {'max_sell_z':>10} {'sell_z_thresh':>14}")
    print(f"  {'-'*58}")
    for label, _, scaled in rows:
        p = scaled.params
        print(f"  {label:<12} {p.min_sell_pct:>8.0%}  {p.max_sell_pct:>8.0%}  "
              f"{p.max_sell_z:>9.1f}  {p.sell_z_threshold:>13.2f}")


if __name__ == "__main__":
    main()
