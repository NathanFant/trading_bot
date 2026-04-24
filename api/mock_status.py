"""
Returns paper-trading mock state as JSON.
Public endpoint — no auth needed, no secrets in response.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage.mock_store as store
from core.micro_backtest import _gross


def _enrich(state: dict) -> dict:
    sol_price = state.get("sol_price") or 0.0
    portfolio = state.get("portfolio_usd", 1000.0)
    start_usd = state.get("start_usd", 1000.0)
    pos       = state.get("position")

    # Unrealized PnL and enriched position
    position_out = None
    unreal       = 0.0
    if pos and sol_price:
        unreal = _gross(pos["dir"], pos["entry_px"], sol_price, pos["contracts"])
        entry  = pos["entry_px"]
        sl     = pos["sl"]
        tp     = pos["tp"]
        full_range = abs(tp - sl)
        if pos["dir"] == "LONG":
            sl_dist_pct = round((sol_price - sl) / sol_price * 100, 2)
            tp_dist_pct = round((tp - sol_price) / sol_price * 100, 2)
            progress    = round(max(0, (sol_price - entry) / max(full_range, 1e-9) * 100), 1)
        else:
            sl_dist_pct = round((sl - sol_price) / sol_price * 100, 2)
            tp_dist_pct = round((sol_price - tp) / sol_price * 100, 2)
            progress    = round(max(0, (entry - sol_price) / max(full_range, 1e-9) * 100), 1)

        position_out = {
            **pos,
            "current_px":      round(sol_price, 4),
            "unrealized_pnl":  round(unreal, 2),
            "sl_dist_pct":     sl_dist_pct,
            "tp_dist_pct":     tp_dist_pct,
            "progress_pct":    min(100.0, progress),
        }

    equity = round(portfolio + unreal, 2)

    # Stats
    st    = state.get("stats", {})
    n     = st.get("num_trades", 0)
    w     = st.get("wins", 0)
    trades = state.get("trades", [])
    pf_wins = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
    pf_loss = abs(sum(t["net_pnl"] for t in trades if t["net_pnl"] <= 0))

    # SOL B&H comparison
    sol_start = state.get("sol_price_at_start")
    sol_bh_pct = round((sol_price / sol_start - 1) * 100, 2) \
                 if sol_start and sol_price else None

    return {
        "timestamp":          int(time.time()),
        "portfolio_usd":      equity,
        "cash_usd":           round(portfolio, 2),
        "start_usd":          start_usd,
        "pnl_pct":            round((equity - start_usd) / max(start_usd, 1) * 100, 2),
        "sol_price":          round(sol_price, 4) if sol_price else None,
        "sol_bh_pct":         sol_bh_pct,
        "position":           position_out,
        "indicator_state":    state.get("indicator_state"),
        "trades":             list(reversed(trades[-50:])),
        "equity_history":     state.get("equity_history", []),
        "stats": {
            "num_trades":       n,
            "win_rate":         round(w / n * 100, 1) if n > 0 else 0.0,
            "profit_factor":    round(pf_wins / pf_loss, 2) if pf_loss > 0 else None,
            "total_fees":       round(st.get("total_fees", 0), 2),
            "max_drawdown_pct": round(st.get("max_drawdown_pct", 0), 2),
        },
        "last_cycle_ts":      state.get("last_cycle_ts", 0),
        "last_cycle_result":  state.get("last_cycle_result", {}),
    }


def app(environ, start_response):
    state  = store.load()
    result = _enrich(state)
    start_response("200 OK", [
        ("Content-Type", "application/json"),
        ("Access-Control-Allow-Origin", "*"),
        ("Cache-Control", "no-store, max-age=0"),
    ])
    return [json.dumps(result).encode()]
