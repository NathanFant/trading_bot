"""
Thin Upstash Redis client for persistent state on Vercel.
Uses the REST API (no extra packages — just requests).

Falls back silently when KV_REST_API_URL / KV_REST_API_TOKEN are absent,
so local dev continues to use SQLite without any changes.
"""

from __future__ import annotations

import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

_URL   = os.environ.get("KV_REST_API_URL", "")
_TOKEN = os.environ.get("KV_REST_API_TOKEN", "")


def available() -> bool:
    return bool(_URL and _TOKEN)


def _cmd(*args: str) -> object:
    r = requests.post(
        _URL,
        headers={"Authorization": f"Bearer {_TOKEN}"},
        json=list(args),
        timeout=5,
    )
    r.raise_for_status()
    return r.json().get("result")


def kv_get(key: str) -> object | None:
    if not available():
        return None
    try:
        result = _cmd("GET", key)
        return json.loads(result) if isinstance(result, str) else result
    except Exception as exc:
        logger.warning("KV get %s failed: %s", key, exc)
        return None


def kv_set(key: str, value: object) -> None:
    if not available():
        return
    try:
        _cmd("SET", key, json.dumps(value))
    except Exception as exc:
        logger.warning("KV set %s failed: %s", key, exc)


def kv_push_trade(trade: dict) -> None:  # type: ignore[type-arg]
    """Append trade to recent_trades list, keeping last 50."""
    if not available():
        return
    try:
        _cmd("RPUSH", "recent_trades", json.dumps(trade))
        _cmd("LTRIM", "recent_trades", "-50", "-1")
    except Exception as exc:
        logger.warning("KV push trade failed: %s", exc)


def kv_get_trades() -> list:  # type: ignore[type-arg]
    """Return up to 50 recent trades from KV, newest last."""
    if not available():
        return []
    try:
        result = _cmd("LRANGE", "recent_trades", "0", "-1")
        if not isinstance(result, list):
            return []
        return [json.loads(t) for t in result]
    except Exception as exc:
        logger.warning("KV get trades failed: %s", exc)
        return []
