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


def kv_set_perf_inception(data: dict) -> None:  # type: ignore[type-arg]
    """Store the baseline values when performance tracking starts. Never overwrites."""
    if not available():
        return
    if kv_get("perf_inception") is not None:
        return
    kv_set("perf_inception", data)


def kv_get_perf_inception() -> dict | None:  # type: ignore[type-arg]
    result = kv_get("perf_inception")
    return result if isinstance(result, dict) else None


def kv_push_perf_snapshot(data: dict) -> None:  # type: ignore[type-arg]
    """Append daily performance snapshot, keep last 365."""
    if not available():
        return
    try:
        _cmd("RPUSH", "perf_snapshots", json.dumps(data))
        _cmd("LTRIM", "perf_snapshots", "-365", "-1")
    except Exception as exc:
        logger.warning("KV push perf snapshot failed: %s", exc)


def kv_get_perf_snapshots() -> list:  # type: ignore[type-arg]
    if not available():
        return []
    try:
        result = _cmd("LRANGE", "perf_snapshots", "0", "-1")
        if not isinstance(result, list):
            return []
        return [json.loads(s) for s in result]
    except Exception as exc:
        logger.warning("KV get perf snapshots failed: %s", exc)
        return []


def kv_set_last_cycle(info: dict) -> None:  # type: ignore[type-arg]
    """Persist last cycle result so the dashboard can show when the bot last ran."""
    kv_set("last_cycle", info)


def kv_get_last_cycle() -> dict | None:  # type: ignore[type-arg]
    result = kv_get("last_cycle")
    return result if isinstance(result, dict) else None


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
