"""
Discord webhook notifications.

All functions are fire-and-forget: they log a warning on failure
rather than raising, so a Discord outage never disrupts trading.
"""

from __future__ import annotations

import logging
import time

import requests

from .signals import Signal

logger = logging.getLogger(__name__)

_TIMEOUT = 8


def _post(webhook_url: str, payload: dict) -> None:  # type: ignore[type-arg]
    if not webhook_url:
        return
    try:
        resp = requests.post(webhook_url, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Discord notification failed: %s", exc)


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())


def send_trade(
    webhook_url: str,
    action: str,
    symbol: str,
    quantity: float,
    price: float,
    usd_amount: float,
    signal: Signal,
    dry_run: bool,
) -> None:
    asset = symbol.split("-")[0]
    emoji = "🟢" if action == "BUY" else "🔴"
    mode = " *(DRY RUN)*" if dry_run else ""
    color = 0x00C851 if action == "BUY" else 0xFF4444

    _post(webhook_url, {
        "embeds": [{
            "title": f"{emoji} {action} {asset}{mode}",
            "color": color,
            "fields": [
                {"name": "Quantity",    "value": f"{quantity:.8f} {asset}", "inline": True},
                {"name": "Price",       "value": f"${price:,.2f}",          "inline": True},
                {"name": "USD Value",   "value": f"${usd_amount:,.2f}",     "inline": True},
                {"name": "FGI",         "value": f"{signal.fgi_value} ({signal.z_score:+.2f}σ)", "inline": True},
                {"name": "Confidence",  "value": f"{signal.confidence:.1%}", "inline": True},
                {"name": "Reason",      "value": signal.reason,              "inline": False},
            ],
            "footer": {"text": _ts()},
        }]
    })


def send_error(webhook_url: str, error: str, context: str = "") -> None:
    _post(webhook_url, {
        "embeds": [{
            "title": "⚠️ Bot Error",
            "color": 0xFF8800,
            "description": f"```{error[:1800]}```",
            "fields": [{"name": "Context", "value": context}] if context else [],
            "footer": {"text": _ts()},
        }]
    })


def send_daily_summary(
    webhook_url: str,
    sol_balance: float,
    usd_balance: float,
    sol_price: float,
    total_value: float,
    trades_today: int,
    fgi_value: int,
    fgi_label: str,
) -> None:
    _post(webhook_url, {
        "embeds": [{
            "title": "📊 Daily Summary",
            "color": 0x7289DA,
            "fields": [
                {"name": "Portfolio Value", "value": f"${total_value:,.2f}",     "inline": True},
                {"name": "SOL Balance",     "value": f"{sol_balance:.8f} SOL",   "inline": True},
                {"name": "USD Balance",     "value": f"${usd_balance:,.2f}",     "inline": True},
                {"name": "SOL Price",       "value": f"${sol_price:,.2f}",       "inline": True},
                {"name": "Trades Today",    "value": str(trades_today),          "inline": True},
                {"name": "Fear & Greed",    "value": f"{fgi_value} — {fgi_label}", "inline": True},
            ],
            "footer": {"text": _ts()},
        }]
    })


def send_startup(webhook_url: str, symbol: str, dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "LIVE TRADING"
    _post(webhook_url, {
        "embeds": [{
            "title": f"🚀 Bot Started — {mode}",
            "color": 0x7289DA,
            "description": f"Trading **{symbol}** using Fear & Greed Index strategy.",
            "footer": {"text": _ts()},
        }]
    })
