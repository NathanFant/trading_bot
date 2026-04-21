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

    try:
        import database as db
        from trader import Trader

        db.init_db()
        Trader().run_once()
        start_response("200 OK", [("Content-Type", "application/json")])
        return [b'{"status":"ok"}']
    except Exception as exc:
        logging.exception("Cycle failed: %s", exc)
        body = json.dumps({"error": str(exc)}).encode()
        start_response("500 Internal Server Error", [("Content-Type", "application/json")])
        return [body]
