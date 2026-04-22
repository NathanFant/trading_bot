"""
Vercel serverless function — returns live bot status as JSON.
Used by the dashboard at index.html. Public endpoint (no auth needed — no secrets in response).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

def _fgi_and_signal() -> dict:  # type: ignore[type-arg]
    from fgi import fetch_current, fetch_history, FGIReading
    from signals import BayesianUpdater, compute_signal
    from kv import kv_get

    history_days = int(os.environ.get("FGI_HISTORY_DAYS", "55"))
    cmc_key = os.environ.get("COINMARKETCAP_API_KEY", "")
    history = fetch_history(history_days, coinmarketcap_api_key=cmc_key)
    current = fetch_current(coinmarketcap_api_key=cmc_key)

    state = kv_get("bayesian_state")
    bayesian = BayesianUpdater.from_state(state) if state else BayesianUpdater()

    signal = compute_signal(
        history, current, bayesian,
        buy_z_threshold=float(os.environ.get("BUY_Z_THRESHOLD", "-1.95")),
        sell_z_threshold=float(os.environ.get("SELL_Z_THRESHOLD", "2.65")),
    )
    return {
        "value": current.value,
        "label": current.label,
        "z_score": round(signal.z_score, 3),
        "mean": round(signal.fgi_mean, 1),
        "std": round(signal.fgi_std, 1),
        "signal": signal.action,
        "confidence": round(signal.confidence, 3),
        "reason": signal.reason,
    }


def _portfolio() -> dict:  # type: ignore[type-arg]
    from robinhood import RobinhoodClient
    symbol = os.environ.get("SYMBOL", "SOL-USD")
    asset = symbol.split("-")[0]
    client = RobinhoodClient(
        api_key=os.environ["ROBINHOOD_API_KEY"],
        private_key_b64=os.environ["ROBINHOOD_PRIVATE_KEY"],
    )
    acct = client.get_account()
    holding = client.get_holding(asset)
    bid, ask = client.get_best_bid_ask(symbol)
    price = (bid + ask) / 2
    sol_qty = holding.total_quantity if holding else 0.0
    sol_value = sol_qty * price
    return {
        "cash": round(acct.buying_power, 2),
        "sol_qty": round(sol_qty, 8),
        "sol_value": round(sol_value, 2),
        "total": round(acct.buying_power + sol_value, 2),
        "sol_price": round(price, 2),
    }


def _bayesian() -> dict | None:  # type: ignore[type-arg]
    from kv import kv_get
    from signals import BayesianUpdater
    state = kv_get("bayesian_state")
    if not state:
        return None
    b = BayesianUpdater.from_state(state)
    return {
        "buy_confidence": round(b.confidence("BUY"), 3),
        "sell_confidence": round(b.confidence("SELL"), 3),
    }


def app(environ, start_response):  # type: ignore[type-arg]
    result: dict = {  # type: ignore[type-arg]
        "timestamp": int(time.time()),
        "dry_run": os.environ.get("DRY_RUN", "true").lower() not in ("false", "0", "no"),
        "symbol": os.environ.get("SYMBOL", "SOL-USD"),
        "config": {
            "min_buy_pct": float(os.environ.get("MIN_BUY_PCT", "0.24")),
            "max_buy_pct": float(os.environ.get("MAX_BUY_PCT", "0.74")),
            "sell_pct": float(os.environ.get("SELL_PCT", "0.65")),
            "buy_z": float(os.environ.get("BUY_Z_THRESHOLD", "-1.95")),
            "sell_z": float(os.environ.get("SELL_Z_THRESHOLD", "2.65")),
            "min_confidence": float(os.environ.get("MIN_CONFIDENCE", "0.53")),
        },
    }

    try:
        result["fgi"] = _fgi_and_signal()
    except Exception as exc:
        logging.exception("FGI fetch failed")
        result["fgi"] = {"error": str(exc)}

    try:
        result["portfolio"] = _portfolio()
    except Exception as exc:
        logging.exception("Portfolio fetch failed")
        result["portfolio"] = {"error": str(exc)}

    try:
        result["bayesian"] = _bayesian()
    except Exception as exc:
        result["bayesian"] = None

    try:
        from kv import kv_get_trades
        result["trades"] = list(reversed(kv_get_trades()))  # newest first
    except Exception:
        result["trades"] = []

    try:
        from kv import kv_get_last_cycle
        result["last_cycle"] = kv_get_last_cycle()
    except Exception:
        result["last_cycle"] = None

    start_response("200 OK", [
        ("Content-Type", "application/json"),
        ("Access-Control-Allow-Origin", "*"),
    ])
    return [json.dumps(result).encode()]
