"""
Persistent state for the paper-trading mock.

Primary: Upstash Redis KV (works on Vercel — serverless functions have no
         writable filesystem).
Fallback: local JSON file at data/mock_state.json (for local dev when KV env
          vars are absent).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from storage import kv as _kv

KV_KEY     = "mock_state"
STATE_FILE = Path(__file__).parent.parent / "data" / "mock_state.json"

_DEFAULT: dict[str, Any] = {
    "start_usd":          1000.0,
    "portfolio_usd":      1000.0,
    "position":           None,
    "trades":             [],
    "equity_history":     [],
    "stats": {
        "num_trades":       0,
        "wins":             0,
        "losses":           0,
        "total_fees":       0.0,
        "gross_pnl":        0.0,
        "peak_usd":         1000.0,
        "max_drawdown_pct": 0.0,
    },
    "last_bar_ts":        0,
    "last_cycle_ts":      0,
    "last_cycle_result":  {},
    "indicator_state":    None,
    "sol_price":          None,
    "sol_price_at_start": None,
    "sol_start_ts":       None,
}


def _default() -> dict[str, Any]:
    s = _DEFAULT.copy()
    s["stats"] = _DEFAULT["stats"].copy()
    return s


def load() -> dict[str, Any]:
    if _kv.available():
        state = _kv.kv_get(KV_KEY)
        if isinstance(state, dict):
            return state
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return _default()


def save(state: dict[str, Any]) -> None:
    if _kv.available():
        _kv.kv_set(KV_KEY, state)
    else:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))


def reset() -> dict[str, Any]:
    state = _default()
    save(state)
    return state
