"""
Vercel serverless function — runs one trading cycle.
Exposed as GET /api/cycle, triggered hourly by cron-job.org.
Protected by CRON_SECRET so random requests can't trigger trades.
"""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

_CRON_SECRET = os.environ.get("CRON_SECRET", "")


def app(environ, start_response):
    """WSGI entry point — Vercel looks for a callable named `app`."""
    auth = environ.get("HTTP_AUTHORIZATION", "")
    if _CRON_SECRET and auth != f"Bearer {_CRON_SECRET}":
        start_response("401 Unauthorized", [("Content-Type", "application/json")])
        return [b'{"error":"unauthorized"}']

    import time
    import requests as _req
    try:
        import database as db
        from trader import Trader
        from kv import kv_set_last_cycle, kv_set_perf_inception, kv_push_perf_snapshot

        db.init_db()
        trader = Trader()
        trader.run_once()

        now = int(time.time())

        kv_set_last_cycle({
            "timestamp": now,
            "last_signal": trader.last_signal,
            "last_skip_reason": trader.last_skip_reason,
        })

        # Fetch benchmark prices for performance tracking
        btc_price = None
        voo_price = None
        try:
            r = _req.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
                timeout=8,
            )
            btc_price = float(r.json()["bitcoin"]["usd"])
        except Exception as e:
            logging.warning("BTC price fetch failed: %s", e)
        try:
            r = _req.get(
                "https://query1.finance.yahoo.com/v8/finance/chart/VOO?interval=1d&range=1d",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=8,
            )
            voo_price = float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
        except Exception as e:
            logging.warning("VOO price fetch failed: %s", e)

        if trader.last_portfolio_usd > 0:
            snapshot = {
                "timestamp": now,
                "bot_usd": round(trader.last_portfolio_usd, 2),
                "btc_price": btc_price,
                "voo_price": voo_price,
            }
            kv_push_perf_snapshot(snapshot)
            kv_set_perf_inception(snapshot)  # no-op if already set

        body = json.dumps({
            "status": "ok",
            "signal": trader.last_signal,
            "skip_reason": trader.last_skip_reason,
        }).encode()
        start_response("200 OK", [("Content-Type", "application/json")])
        return [body]
    except Exception as exc:
        logging.exception("Cycle failed: %s", exc)
        body = json.dumps({"error": str(exc)}).encode()
        start_response("500 Internal Server Error", [("Content-Type", "application/json")])
        return [body]
