"""
Live perp trading engine — EMA-ADX+Regime on SOL perp 30m.

Based on the paper-trading winner: EMA(9/21) cross + ADX≥18 + EMA(21)>EMA(55) trend
+ 6h EMA(21) regime filter + ATR health gate.
Result: +26.5%, Sharpe 19.54, MaxDD 8.0%, WR 45.2% on 90-day SOL data.

Call run_cycle() once per 30m bar close from a cron or script.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env.local")

from core.coinbase import CoinbaseClient
from core.micro_backtest import (
    CONTRACT_SIZE, LEVERAGE, TAKER_FEE_PCT, NFA_FEE, MARGIN_FRACTION,
    _fees, _gross, _num_contracts,
    ema, atr, adx,
)
import storage.database as db  # Use real DB for live

log = logging.getLogger(__name__)

PRODUCT_ID  = "SLP-20DEC30-CDE"
SL_MULT     = 1.5
TP_MULT     = 4.0
ADX_MIN     = 18.0
ATR_MA_P    = 30
ATR_LOW     = 0.5   # skip if ATR < 0.5× ATR-MA (dead market)
ATR_HIGH    = 2.5   # skip if ATR > 2.5× ATR-MA (volatility spike)


def _fetch_candles(client: CoinbaseClient, gran_str: str, gran_sec: int, n_bars: int) -> pd.DataFrame:
    """Fetch the last n_bars of completed candles (excludes current forming bar)."""
    now   = int(time.time())
    end   = now - gran_sec            # exclude current forming bar
    start = end - (n_bars + 10) * gran_sec
    path  = (f"/api/v3/brokerage/products/{PRODUCT_ID}/candles"
             f"?start={start}&end={end}&granularity={gran_str}")
    data  = client._get(path)
    rows  = data.get("candles", [])
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["start", "low", "high", "open", "close", "volume"])
    for col in df.columns:
        df[col] = df[col].astype(float)
    df["start"] = df["start"].astype(int)
    df = df.sort_values("start").drop_duplicates("start").reset_index(drop=True)
    return df.tail(n_bars).reset_index(drop=True)


def _compute_signals(df30: pd.DataFrame, df6h: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, pd.Series]:
    """
    EMA-ADX+Regime strategy (90-day backtest winner: +26.5%, Sharpe 19.54).
    Adds ATR health gate to skip entries during extreme quiet or volatility spikes.

    Returns (signals, atr_arr, adx_series).
    """
    c = df30["close"]

    ef    = ema(c, 9)
    es    = ema(c, 21)
    et    = ema(c, 55)
    adx_s = adx(df30, 14)
    atr_s = atr(df30, 14)

    # ATR health: skip dead markets and extreme spike conditions
    atr_ma = atr_s.rolling(ATR_MA_P).mean()
    atr_ok = (atr_s > atr_ma * ATR_LOW) & (atr_s < atr_ma * ATR_HIGH)

    # 6h regime direction aligned to 30m bars
    c_reg   = df6h["close"]
    e_reg   = ema(c_reg, 21)
    reg_dir = (c_reg > e_reg).astype(int).to_numpy()
    ts_reg  = df6h["start"].to_numpy()
    ts_sig  = df30["start"].to_numpy()

    aligned = np.zeros(len(df30), dtype=int)
    j = 0
    for i, ts in enumerate(ts_sig):
        while j + 1 < len(ts_reg) and ts_reg[j + 1] <= ts:
            j += 1
        aligned[i] = reg_dir[j]

    cross_up   = (ef > es) & (ef.shift(1) <= es.shift(1))
    cross_down = (ef < es) & (ef.shift(1) >= es.shift(1))
    strong     = adx_s >= ADX_MIN
    up_trend   = es > et
    dn_trend   = es < et

    sigs = np.zeros(len(df30))
    sigs[cross_up   & strong & up_trend & (aligned == 1) & atr_ok] = +1
    sigs[cross_down & strong & dn_trend & (aligned == 0) & atr_ok] = -1

    return sigs, atr_s.to_numpy(), adx_s


def _indicator_snapshot(df30: pd.DataFrame, df6h: pd.DataFrame,
                        sigs: np.ndarray, adx_s: pd.Series) -> dict[str, Any]:
    """Build the indicator state dict shown in the dashboard."""
    c30      = df30["close"]
    ef_last  = float(ema(c30, 9).iloc[-1])
    es_last  = float(ema(c30, 21).iloc[-1])
    et_last  = float(ema(c30, 55).iloc[-1])
    adx_last = float(adx_s.iloc[-1])

    c_reg      = df6h["close"]
    e_reg_last = float(ema(c_reg, 21).iloc[-1])
    regime     = "BULL" if float(c_reg.iloc[-1]) > e_reg_last else "BEAR"

    vol_last    = float(df30["volume"].iloc[-1])
    vol_ma_last = float(df30["volume"].rolling(20).mean().iloc[-1])

    return {
        "regime":          regime,
        "adx":             round(adx_last, 1),
        "ema_fast":        round(ef_last, 3),
        "ema_slow":        round(es_last, 3),
        "ema_trend":       round(et_last, 3),
        "ema_aligned":     ef_last > es_last,
        "vol_surge":       vol_last > vol_ma_last * 1.1,
        "latest_bar_ts":   int(df30["start"].iloc[-1]),
        "last_signal":     int(sigs[-1]),
    }


def run_cycle(dry_run: bool = True) -> dict[str, Any]:
    """
    Run one live perp trading cycle. Safe to call from cron or script.

    Flow:
      1. Fetch latest 30m + 6h candles from Coinbase
      2. Get live SOL price
      3. Check open position against live price (SL/TP)
      4. Compute signals on last completed bar
      5. Open new position if fresh signal and not already in same direction
      6. Save state and return summary
    """
    db.init_db()
    client   = CoinbaseClient()
    ts_now   = int(time.time())
    result: dict[str, Any] = {"ts": ts_now, "action": "none", "detail": ""}

    # Load state from DB
    state = db.load_perp_state() or {
        "portfolio_usd": 1000.0,
        "start_usd": 1000.0,
        "position": None,
        "trades": [],
        "stats": {"num_trades": 0, "wins": 0, "losses": 0, "total_fees": 0.0, "gross_pnl": 0.0, "max_drawdown_pct": 0.0},
        "equity_history": [],
        "last_bar_ts": 0,
        "sol_price": None,
        "sol_price_at_start": None,
        "sol_start_ts": None,
        "indicator_state": {},
        "last_cycle_ts": 0,
        "last_cycle_result": {},
    }

    # ── 1. Fetch candles ──────────────────────────────────────────────────────
    try:
        df30 = _fetch_candles(client, "THIRTY_MINUTE", 1800,  150)
        df6h = _fetch_candles(client, "SIX_HOUR",      21600,  50)
    except Exception as exc:
        log.error("Candle fetch failed: %s", exc)
        result.update(action="error", detail=str(exc))
        state.update(last_cycle_ts=ts_now, last_cycle_result=result)
        db.save_perp_state(state)
        return result

    if df30.empty or df6h.empty or len(df30) < 60:
        result.update(action="error", detail="insufficient candle data")
        state.update(last_cycle_ts=ts_now, last_cycle_result=result)
        db.save_perp_state(state)
        return result

    # ── 2. Live price ─────────────────────────────────────────────────────────
    try:
        sol_price = client.get_mid_price(PRODUCT_ID)
    except Exception:
        sol_price = float(df30["close"].iloc[-1])

    state["sol_price"] = round(sol_price, 4)

    if state.get("sol_price_at_start") is None:
        state["sol_price_at_start"] = sol_price
        state["sol_start_ts"]       = ts_now

    # ── 3. Compute signals ────────────────────────────────────────────────────
    sigs, atr_arr, adx_s = _compute_signals(df30, df6h)

    latest_bar_ts = int(df30["start"].iloc[-1])
    last_sig      = int(sigs[-1])
    last_atr      = float(atr_arr[-1])

    state["indicator_state"] = _indicator_snapshot(df30, df6h, sigs, adx_s)

    portfolio = state["portfolio_usd"]
    pos       = state.get("position")

    # ── 4. Check open position ────────────────────────────────────────────────
    if pos is not None:
        dir_ = pos["dir"]
        sl   = pos["sl"]
        tp   = pos["tp"]

        sl_hit = (dir_ == "LONG"  and sol_price <= sl) or \
                 (dir_ == "SHORT" and sol_price >= sl)
        tp_hit = (dir_ == "LONG"  and sol_price >= tp) or \
                 (dir_ == "SHORT" and sol_price <= tp)

        if sl_hit or tp_hit:
            exit_px = sl if sl_hit else tp
            reason  = "SL" if sl_hit else "TP"
            # Close position at market
            if not dry_run:
                try:
                    if dir_ == "LONG":
                        order = client.sell_asset_amount(PRODUCT_ID, pos["contracts"])
                    else:
                        order = client.buy_usd_amount(PRODUCT_ID, pos["contracts"] * sol_price)  # Approximate USD amount
                    # Wait for fill
                    time.sleep(2)
                    order = client.get_order(order.order_id)
                    exit_px = order.average_filled_price or sol_price
                except Exception as exc:
                    log.error("Failed to close position: %s", exc)
                    result.update(action="error", detail=f"Close failed: {exc}")
                    state.update(last_cycle_ts=ts_now, last_cycle_result=result)
                    db.save_perp_state(state)
                    return result
            g   = _gross(dir_, pos["entry_px"], exit_px, pos["contracts"])
            fee = _fees(pos["entry_px"], exit_px, pos["contracts"])
            net = g - fee
            portfolio += net
            state["trades"].append({
                "dir":         dir_,
                "entry_px":    pos["entry_px"],
                "exit_px":     round(exit_px, 4),
                "contracts":   pos["contracts"],
                "gross_pnl":   round(g, 2),
                "fees":        round(fee, 2),
                "net_pnl":     round(net, 2),
                "exit_reason": reason,
                "entry_ts":    pos["entry_ts"],
                "exit_ts":     ts_now,
            })
            state["trades"] = state["trades"][-100:]
            st = state["stats"]
            st["num_trades"] += 1
            st["total_fees"]  = round(st["total_fees"] + fee, 2)
            st["gross_pnl"]   = round(st["gross_pnl"]  + g,   2)
            if net > 0:
                st["wins"]   += 1
            else:
                st["losses"] += 1
            peak = max(st.get("peak_usd", state["start_usd"]), portfolio)
            st["peak_usd"] = peak
            dd = (peak - portfolio) / peak * 100 if peak > 0 else 0
            st["max_drawdown_pct"] = round(max(st.get("max_drawdown_pct", 0), dd), 2)
            state["position"]      = None
            state["portfolio_usd"] = round(portfolio, 2)
            result.update(
                action=f"close_{reason.lower()}",
                detail=f"Closed {dir_} @ {exit_px:.2f} ({reason}), net ${state['trades'][-1]['net_pnl']:+.2f}",
            )
            log.info(result["detail"])

    # ── 5. Equity snapshot ────────────────────────────────────────────────────
    unreal = 0.0
    if state.get("position"):
        p2     = state["position"]
        unreal = _gross(p2["dir"], p2["entry_px"], sol_price, p2["contracts"])
    equity_now = round(portfolio + unreal, 2)
    state["equity_history"].append({"ts": ts_now, "usd": equity_now})
    state["equity_history"] = state["equity_history"][-2016:]  # ~42 days at 30m

    # ── 6. New signal ─────────────────────────────────────────────────────────
    if last_sig != 0 and latest_bar_ts > state.get("last_bar_ts", 0):
        state["last_bar_ts"] = latest_bar_ts
        new_dir = "LONG" if last_sig > 0 else "SHORT"

        cur_pos = state.get("position")
        if cur_pos and cur_pos["dir"] == new_dir:
            result.setdefault("action", "hold")
        elif portfolio >= 50:
            # Flip: close opposite position if open
            if cur_pos:
                if not dry_run:
                    try:
                        if cur_pos["dir"] == "LONG":
                            order = client.sell_asset_amount(PRODUCT_ID, cur_pos["contracts"])
                        else:
                            order = client.buy_usd_amount(PRODUCT_ID, cur_pos["contracts"] * sol_price)
                        time.sleep(2)
                        order = client.get_order(order.order_id)
                        exit_px = order.average_filled_price or sol_price
                    except Exception as exc:
                        log.error("Failed to flip position: %s", exc)
                        result.update(action="error", detail=f"Flip failed: {exc}")
                        state.update(last_cycle_ts=ts_now, last_cycle_result=result)
                        db.save_perp_state(state)
                        return result
                else:
                    exit_px = sol_price
                g   = _gross(cur_pos["dir"], cur_pos["entry_px"], exit_px, cur_pos["contracts"])
                fee = _fees(cur_pos["entry_px"], exit_px, cur_pos["contracts"])
                net = g - fee
                portfolio += net
                state["trades"].append({
                    "dir":         cur_pos["dir"],
                    "entry_px":    cur_pos["entry_px"],
                    "exit_px":     round(exit_px, 4),
                    "contracts":   cur_pos["contracts"],
                    "gross_pnl":   round(g, 2),
                    "fees":        round(fee, 2),
                    "net_pnl":     round(net, 2),
                    "exit_reason": "FLIP",
                    "entry_ts":    cur_pos["entry_ts"],
                    "exit_ts":     ts_now,
                })
                state["position"] = None

            # Open new position at market
            ctrs = _num_contracts(portfolio, sol_price)
            if new_dir == "LONG":
                sl_ = sol_price - SL_MULT * last_atr
                tp_ = sol_price + TP_MULT * last_atr
            else:
                sl_ = sol_price + SL_MULT * last_atr
                tp_ = sol_price - TP_MULT * last_atr

            if (new_dir == "LONG" and sl_ < sol_price) or \
               (new_dir == "SHORT" and sl_ > sol_price):
                if not dry_run:
                    try:
                        if new_dir == "LONG":
                            order = client.buy_usd_amount(PRODUCT_ID, ctrs * sol_price)
                        else:
                            order = client.sell_asset_amount(PRODUCT_ID, ctrs)
                        time.sleep(2)
                        order = client.get_order(order.order_id)
                        entry_px = order.average_filled_price or sol_price
                    except Exception as exc:
                        log.error("Failed to open position: %s", exc)
                        result.update(action="error", detail=f"Open failed: {exc}")
                        state.update(last_cycle_ts=ts_now, last_cycle_result=result)
                        db.save_perp_state(state)
                        return result
                else:
                    entry_px = sol_price
                state["position"] = {
                    "dir":       new_dir,
                    "entry_px":  round(entry_px, 4),
                    "sl":        round(sl_, 4),
                    "tp":        round(tp_, 4),
                    "contracts": ctrs,
                    "entry_ts":  ts_now,
                }
                state["portfolio_usd"] = round(portfolio, 2)
                result.update(
                    action=f"open_{new_dir.lower()}",
                    detail=(f"Opened {new_dir} {ctrs}c @ {entry_px:.2f} "
                            f"| SL {sl_:.2f} | TP {tp_:.2f}"),
                )
                log.info(result["detail"])

    state["portfolio_usd"]     = round(portfolio, 2)
    state["last_cycle_ts"]     = ts_now
    state["last_cycle_result"] = result
    db.save_perp_state(state)
    return result</content>
<parameter name="filePath">/workspaces/trading_bot/core/perp_trader.py