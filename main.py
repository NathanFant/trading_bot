"""
Entry point for the crypto trading bot.

Usage:
  python main.py                  # run live trading loop (DRY_RUN=true by default)
  python main.py --backtest       # run backtest + Monte Carlo, then exit
  python main.py --status         # print account info and recent trades, then exit

Environment:
  All config is read from .env — see .env.example for full reference.
  Set DRY_RUN=false to enable live order execution.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env before any other local imports so env vars are available at import time
load_dotenv(Path(__file__).parent / ".env.local")


def _setup_logging() -> None:
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    fmt = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                Path(os.environ.get("DATA_DIR", Path(__file__).parent)) / "bot.log",
                encoding="utf-8",
            ),
        ],
    )
    # Quiet noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def cmd_trade() -> None:
    import database as db
    from trader import Trader

    db.init_db()
    Trader().run()


def cmd_backtest(n_simulations: int) -> None:
    from backtest import load_aligned_data, print_monte_carlo_summary, run_monte_carlo

    print(f"Loading historical data…")
    bars = load_aligned_data(days=365)
    if len(bars) < 60:
        print(f"ERROR: Only {len(bars)} aligned bars — need at least 60. Check network access.")
        sys.exit(1)

    print(f"Running {n_simulations} Monte Carlo simulations on {len(bars)} days of data…")
    results = run_monte_carlo(bars, n_simulations=n_simulations)
    print_monte_carlo_summary(results)


def cmd_status() -> None:
    import database as db
    from robinhood import RobinhoodClient

    db.init_db()

    client = RobinhoodClient(
        api_key=os.environ["ROBINHOOD_API_KEY"],
        private_key_b64=os.environ["ROBINHOOD_PRIVATE_KEY"],
    )
    symbol = os.environ.get("SYMBOL", "SOL-USD")
    asset = symbol.split("-")[0]

    print("\n── Account ──────────────────────────────────────")
    acct = client.get_account()
    print(f"  Buying power : ${acct.buying_power:,.2f} {acct.currency}")

    holding = client.get_holding(asset)
    if holding:
        print(f"  {asset} held    : {holding.total_quantity:.8f}")
        print(f"  {asset} avail   : {holding.quantity_available:.8f}")
    else:
        print(f"  {asset} held    : 0")

    bid, ask = client.get_best_bid_ask(symbol)
    print(f"  {symbol} bid  : ${bid:,.2f}")
    print(f"  {symbol} ask  : ${ask:,.2f}")

    print("\n── Recent Trades ─────────────────────────────────")
    trades = db.get_recent_trades(10)
    if not trades:
        print("  (no trades recorded yet)")
    for t in trades:
        import time
        ts = time.strftime("%Y-%m-%d %H:%M", time.gmtime(t["timestamp"]))
        dr = " [DRY]" if t["dry_run"] else ""
        print(f"  {ts}  {t['action']:4}  {t['quantity']:.6f} {asset}  "
              f"@ ${t['price']:,.2f}  (FGI={t['fgi_value']} z={t['z_score']:.2f}){dr}")

    print("\n── Bayesian State ────────────────────────────────")
    state = db.load_bayesian_state()
    if state:
        from signals import BayesianUpdater
        b = BayesianUpdater.from_state(state)
        print(f"  BUY  confidence : {b.confidence('BUY'):.1%}  (n={b.effective_sample_size('BUY'):.0f})")
        print(f"  SELL confidence : {b.confidence('SELL'):.1%}  (n={b.effective_sample_size('SELL'):.0f})")
    else:
        print("  (no Bayesian state saved yet — using prior)")
    print()


def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(description="FGI Crypto Trading Bot")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--backtest", action="store_true",
                       help="Run backtest + Monte Carlo simulation and exit")
    group.add_argument("--status", action="store_true",
                       help="Print account info and recent trades, then exit")
    parser.add_argument("--simulations", type=int, default=200,
                        help="Number of Monte Carlo simulations (default: 200)")
    args = parser.parse_args()

    # Validate required env vars
    missing = [v for v in ("ROBINHOOD_API_KEY", "ROBINHOOD_PRIVATE_KEY") if not os.environ.get(v)]
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        print("Run public_key.py to generate keys, then fill in .env.")
        sys.exit(1)

    if args.backtest:
        cmd_backtest(args.simulations)
    elif args.status:
        cmd_status()
    else:
        cmd_trade()


if __name__ == "__main__":
    main()
