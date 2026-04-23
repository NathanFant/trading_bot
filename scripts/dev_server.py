"""
Local development server — serves /api/* routes via Python WSGI apps.
Matches what Vercel does in production.

Usage:
  python scripts/dev_server.py           # serve + auto-cycle every 30m
  python scripts/dev_server.py --no-cron # serve only, run cycles manually

Port 3000 matches the Vite proxy (vite.config.ts → proxy '/api' → 3000).
Run alongside: npm run dev
"""

from __future__ import annotations

import argparse
import importlib
import io
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env.local")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

ROUTES = {
    "/api/mock_status": "api.mock_status",
    "/api/mock_cycle":  "api.mock_cycle",
    "/api/status":      "api.status",
    "/api/cycle":       "api.cycle",
}


def _call_wsgi(module_name: str, method: str, path: str, body: bytes) -> tuple[str, list, bytes]:
    mod  = importlib.import_module(module_name)
    app  = mod.app
    out  = []
    status_holder: list[str] = []
    headers_holder: list[list] = []

    environ = {
        "REQUEST_METHOD":  method,
        "PATH_INFO":       path,
        "CONTENT_LENGTH":  str(len(body)),
        "wsgi.input":      io.BytesIO(body),
        "wsgi.errors":     sys.stderr,
    }

    def start_response(status, headers):
        status_holder.append(status)
        headers_holder.append(headers)

    result = app(environ, start_response)
    body_out = b"".join(result)
    return (
        status_holder[0] if status_holder else "200 OK",
        headers_holder[0] if headers_holder else [],
        body_out,
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("HTTP %s", fmt % args)

    def _handle(self, method: str):
        path      = self.path.split("?")[0]
        mod_name  = ROUTES.get(path)
        if mod_name is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')
            return

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b""

        try:
            status, headers, resp_body = _call_wsgi(mod_name, method, path, body)
        except Exception as exc:
            log.exception("Handler error")
            status    = "500 Internal Server Error"
            headers   = [("Content-Type", "application/json")]
            resp_body = json.dumps({"error": str(exc)}).encode()

        code = int(status.split(" ", 1)[0])
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for k, v in headers:
            if k.lower() not in ("access-control-allow-origin",):
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp_body)

    def do_GET(self):    self._handle("GET")
    def do_POST(self):   self._handle("POST")
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()


def _auto_cycle(interval_sec: int = 1800):
    """Background thread: run a cycle at startup and every interval_sec after."""
    from core.mock_trader import run_cycle
    log.info("Auto-cycle: first run now, then every %ds", interval_sec)
    while True:
        try:
            r = run_cycle()
            log.info("Auto-cycle result: %s | %s", r.get("action"), r.get("detail"))
        except Exception as exc:
            log.error("Auto-cycle failed: %s", exc)
        time.sleep(interval_sec)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",    type=int, default=3000)
    parser.add_argument("--no-cron", action="store_true", help="Disable auto-cycle background thread")
    parser.add_argument("--interval", type=int, default=1800, help="Cycle interval seconds (default 1800)")
    args = parser.parse_args()

    if not args.no_cron:
        t = threading.Thread(target=_auto_cycle, args=(args.interval,), daemon=True)
        t.start()
        log.info("Auto-cycle thread started (every %ds)", args.interval)

    server = HTTPServer(("", args.port), Handler)
    log.info("Dev server listening on http://localhost:%d", args.port)
    log.info("Run: npm run dev  (Vite on :5173, proxies /api → :%d)", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
