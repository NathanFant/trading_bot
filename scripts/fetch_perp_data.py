"""
Download and cache historical candle data for Coinbase perpetual futures.

Saves to data/perp_candles/{ASSET}_{TF}.csv — re-reads on subsequent runs
instead of hitting the API again (pass --force to re-download).

Usage:
  python scripts/fetch_perp_data.py               # default: SOL, 90 days
  python scripts/fetch_perp_data.py --force        # force re-download
  python scripts/fetch_perp_data.py --days 30      # shorter window
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

from core.coinbase import CoinbaseClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "perp_candles"

# (label, CB granularity string, seconds per candle, CB-limit candles/request)
GRANULARITIES = [
    ("1m",  "ONE_MINUTE",     60,    300),
    ("5m",  "FIVE_MINUTE",    300,   300),
    ("15m", "FIFTEEN_MINUTE", 900,   300),
    ("30m", "THIRTY_MINUTE",  1800,  300),
    ("1h",  "ONE_HOUR",       3600,  300),
    ("6h",  "SIX_HOUR",       21600, 300),
]

# Asset → Coinbase perp product ID
PERP_PRODUCTS = {
    "SOL": "SLP-20DEC30-CDE",
    "BTC": "BIP-20DEC30-CDE",
    "ETH": "ETP-20DEC30-CDE",
}


def _fetch_chunk(client: CoinbaseClient, product_id: str, start: int, end: int, gran: str) -> list[dict]:
    path = (f"/api/v3/brokerage/products/{product_id}/candles"
            f"?start={start}&end={end}&granularity={gran}")
    try:
        data = client._get(path)
        return data.get("candles", [])
    except Exception as exc:
        log.warning("Candle fetch failed [%s %s-%s]: %s", gran, start, end, exc)
        return []


def fetch_candles(client: CoinbaseClient, product_id: str, days: int,
                  gran_label: str, gran_str: str, gran_sec: int, limit: int) -> pd.DataFrame:
    """Paginate backwards from now to fetch `days` of candles."""
    window = limit * gran_sec
    now = int(time.time())
    cutoff = now - days * 86400

    all_rows: list[dict] = []
    current_end = now
    req = 0

    while current_end > cutoff:
        current_start = max(current_end - window, cutoff)
        chunk = _fetch_chunk(client, product_id, current_start, current_end, gran_str)
        all_rows.extend(chunk)
        current_end = current_start
        req += 1
        if req % 10 == 0:
            log.info("  ... %d requests done, %d candles so far", req, len(all_rows))
        time.sleep(0.12)  # ~8 req/s, safely under rate limit

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=["start", "low", "high", "open", "close", "volume"])
    df["start"] = df["start"].astype(int)
    for col in ("low", "high", "open", "close", "volume"):
        df[col] = df[col].astype(float)
    df = df.sort_values("start").drop_duplicates("start").reset_index(drop=True)
    return df


def fetch_asset(client: CoinbaseClient, asset: str, days: int,
                granularities: list[tuple], force: bool = False) -> None:
    product_id = PERP_PRODUCTS[asset]
    log.info("Asset: %s  product: %s  days: %d", asset, product_id, days)

    for (label, gran_str, gran_sec, limit) in granularities:
        csv_path = DATA_DIR / f"{asset}_{label}.csv"

        if csv_path.exists() and not force:
            age_hours = (time.time() - csv_path.stat().st_mtime) / 3600
            if age_hours < 6:
                existing = pd.read_csv(csv_path)
                log.info("  %s: skip (file is %.1fh old, %d rows)", label, age_hours, len(existing))
                continue
            log.info("  %s: file is %.1fh old — refreshing", label, age_hours)

        n_requests = max(1, (days * 86400) // (limit * gran_sec) + 1)
        log.info("  %s: fetching ~%d requests…", label, n_requests)
        df = fetch_candles(client, product_id, days, label, gran_str, gran_sec, limit)

        if df.empty:
            log.warning("  %s: no data returned", label)
            continue

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(csv_path, index=False)
        log.info("  %s: saved %d rows to %s", label, len(df), csv_path.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", nargs="+", default=["SOL"], choices=["SOL", "BTC", "ETH"])
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--timeframes", nargs="+", default=["1m", "5m", "15m", "30m", "1h", "6h"],
                        choices=["1m", "5m", "15m", "30m", "1h", "6h"])
    parser.add_argument("--force", action="store_true", help="Re-download even if file is fresh")
    args = parser.parse_args()

    selected = [g for g in GRANULARITIES if g[0] in args.timeframes]
    log.info("Fetching %s | %s | %d days", args.assets, [g[0] for g in selected], args.days)

    client = CoinbaseClient()
    for asset in args.assets:
        fetch_asset(client, asset, args.days, selected, force=args.force)

    log.info("Done. Files in %s:", DATA_DIR)
    for f in sorted(DATA_DIR.glob("*.csv")):
        df = pd.read_csv(f)
        log.info("  %s  (%d rows)", f.name, len(df))


if __name__ == "__main__":
    main()
