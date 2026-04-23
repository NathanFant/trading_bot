"""
Vercel serverless function — runs one live perp trading cycle.
Exposed as GET /api/perp_cycle, triggered every 30 min by cron-job.org.
Protected by CRON_SECRET so random requests can't trigger it.

Set up cron-job.org to hit:
  GET https://your-app.vercel.app/api/perp_cycle
  Header: Authorization: Bearer <CRON_SECRET>
  Schedule: every 30 minutes
"""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

_CRON_SECRET = os.environ.get("CRON_SECRET", "")


def app(environ, start_response):
    auth = environ.get("HTTP_AUTHORIZATION", "")
    if _CRON_SECRET and auth != f"Bearer {_CRON_SECRET}":
        start_response("401 Unauthorized", [
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
        ])
        return [b'{"error":"unauthorized"}']

    from core.perp_trader import run_cycle
    try:
        result = run_cycle(dry_run=False)
        status = "200 OK"
    except Exception as exc:
        logging.exception("perp_cycle failed")
        result = {"error": str(exc)}
        status = "500 Internal Server Error"

    start_response(status, [
        ("Content-Type", "application/json"),
        ("Access-Control-Allow-Origin", "*"),
    ])
    return [json.dumps(result).encode()]