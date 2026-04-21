"""
Vercel serverless function — runs one trading cycle.

Called by Vercel Cron every hour, or any external cron hitting GET /api/cycle.
Protected by CRON_SECRET so random requests can't trigger trades.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler

# Ensure repo root is on the path so local modules are importable
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


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        # Vercel passes the secret via Authorization header
        auth = self.headers.get("Authorization", "")
        if _CRON_SECRET and auth != f"Bearer {_CRON_SECRET}":
            self._respond(401, {"error": "unauthorized"})
            return

        try:
            import database as db
            from trader import Trader

            db.init_db()
            Trader().run_once()
            self._respond(200, {"status": "ok"})
        except Exception as exc:
            logging.exception("Cycle failed: %s", exc)
            self._respond(500, {"error": str(exc)})

    def _respond(self, code: int, body: dict) -> None:  # type: ignore[type-arg]
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass  # Suppress default HTTP log lines; Python logging handles it
