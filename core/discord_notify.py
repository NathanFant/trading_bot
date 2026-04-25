"""
Discord trade alert notifications via webhook.

Fires on position open, close (TP/SL/FLIP), and errors.
All functions are fire-and-forget — failures are logged, never raised.
"""

from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")

_COLOR_LONG  = 0x3fb950   # green
_COLOR_SHORT = 0xf85149   # red
_COLOR_WIN   = 0x3fb950
_COLOR_LOSS  = 0xf85149
_COLOR_FLIP  = 0xe3b341   # yellow
_COLOR_ERR   = 0x8b949e   # grey


def _send(payload: dict) -> None:
    if not _WEBHOOK:
        return
    try:
        r = requests.post(_WEBHOOK, json=payload, timeout=5)
        r.raise_for_status()
    except Exception as exc:
        log.warning("Discord notify failed: %s", exc)


def _pct(equity: float, start: float) -> str:
    p = (equity - start) / max(start, 1) * 100
    return f"{'+' if p >= 0 else ''}{p:.2f}%"


def notify_opened(
    dir_: str, entry_px: float, sl: float, tp: float,
    contracts: int, portfolio: float, start_usd: float,
) -> None:
    color    = _COLOR_LONG if dir_ == "LONG" else _COLOR_SHORT
    sl_dist  = abs(entry_px - sl) / entry_px * 100
    tp_dist  = abs(tp - entry_px) / entry_px * 100
    rr       = tp_dist / sl_dist if sl_dist else 0

    _send({"embeds": [{
        "title": f"{'🟢' if dir_ == 'LONG' else '🔴'} {dir_} opened · {contracts}c @ ${entry_px:.2f}",
        "color": color,
        "fields": [
            {"name": "Stop Loss",    "value": f"${sl:.2f}  (−{sl_dist:.1f}%)", "inline": True},
            {"name": "Take Profit",  "value": f"${tp:.2f}  (+{tp_dist:.1f}%)", "inline": True},
            {"name": "R:R",          "value": f"1 : {rr:.2f}",                 "inline": True},
            {"name": "Portfolio",    "value": f"${portfolio:.2f}  ({_pct(portfolio, start_usd)})", "inline": True},
        ],
        "footer": {"text": "GRACE v2 · SOL perp 30m"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }]})


def notify_closed(
    dir_: str, entry_px: float, exit_px: float, reason: str,
    net_pnl: float, fees: float, portfolio: float, start_usd: float,
) -> None:
    win   = net_pnl > 0
    color = _COLOR_WIN if win else (_COLOR_FLIP if reason == "FLIP" else _COLOR_LOSS)
    icon  = "✅" if win else ("🔄" if reason == "FLIP" else "❌")
    move  = (exit_px - entry_px) / entry_px * 100 * (1 if dir_ == "LONG" else -1)

    _send({"embeds": [{
        "title": f"{icon} {dir_} closed ({reason}) · {'+' if net_pnl >= 0 else ''}${net_pnl:.2f}",
        "color": color,
        "fields": [
            {"name": "Entry → Exit", "value": f"${entry_px:.2f} → ${exit_px:.2f}  ({move:+.2f}%)", "inline": True},
            {"name": "Fees",         "value": f"${fees:.2f}",                                       "inline": True},
            {"name": "Portfolio",    "value": f"${portfolio:.2f}  ({_pct(portfolio, start_usd)})",  "inline": True},
        ],
        "footer": {"text": "GRACE v2 · SOL perp 30m"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }]})


def notify_error(message: str) -> None:
    _send({"embeds": [{
        "title": "⚠️ Bot error",
        "description": message[:1000],
        "color": _COLOR_ERR,
        "footer": {"text": "GRACE v2 · SOL perp 30m"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }]})
