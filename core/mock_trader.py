"""
Live paper-trading engine — GRACE strategy on SOL perp 30m.

GRACE (Gated Regime-Aligned Cross Entry):
  S(t) = sgn(Δ) · 1[cross_event] · 1[ADX≥18] · 1[trend_aligned] · 1[regime_aligned]
  where Δ(t) = EMA(9,t) − EMA(21,t)
  SL = entry − S·1.5·ATR₁₄,  TP = entry + S·4.0·ATR₁₄

Backtest: +26.5%, Sharpe 19.54, MaxDD 8.0%, WR 45.2% on 90-day SOL data.

run_cycle() is safe to call every minute:
  - fast path (every call)  : live price fetch → SL/TP check → equity snapshot
  - slow path (new 30m bar) : 150-bar candle fetch → GRACE signal → open/flip position
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
import storage.mock_store as store

log = logging.getLogger(__name__)

PRODUCT_ID  = "SLP-20DEC30-CDE"
SL_MULT     = 1.5
TP_MULT     = 4.0
ADX_MIN     = 18.0
ATR_MA_P    = 30
ATR_LOW     = 0.5
ATR_HIGH    = 2.5
BAR_SEC     = 1800   # 30-minute bars


def _current_bar_boundary(ts: int) -> int:
    """Return the start timestamp of the most recently *completed* 30m bar."""
    return (ts // BAR_SEC) * BAR_SEC - BAR_SEC


def _fetch_candles(client: CoinbaseClient, gran_str: str, gran_sec: int, n_bars: int) -> pd.DataFrame:
    """Fetch the last n_bars of completed candles (excludes current forming bar)."""
    now   = int(time.time())
    end   = now - gran_sec
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
    GRACE signal computation.
    Returns (signals, atr_arr, adx_series).
    """
    c = df30["close"]

    ef    = ema(c, 9)
    es    = ema(c, 21)
    et    = ema(c, 55)
    adx_s = adx(df30, 14)
    atr_s = atr(df30, 14)

    atr_ma = atr_s.rolling(ATR_MA_P).mean()
    atr_ok = (atr_s > atr_ma * ATR_LOW) & (atr_s < atr_ma * ATR_HIGH)

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
        "regime":        regime,
        "adx":           round(adx_last, 1),
        "ema_fast":      round(ef_last, 3),
        "ema_slow":      round(es_last, 3),
        "ema_trend":     round(et_last, 3),
        "ema_aligned":   ef_last > es_last,
        "vol_surge":     vol_last > vol_ma_last * 1.1,
        "latest_bar_ts": int(df30["start"].iloc[-1]),
        "last_signal":   int(sigs[-1]),
    }


def _close_position(state: dict, exit_px: float, reason: str, ts_now: int) -> float:
    """Close open position, record trade, update stats. Returns updated portfolio value."""
    pos       = state["position"]
    portfolio = state["portfolio_usd"]
    dir_      = pos["dir"]
    ep        = pos["entry_px"]
    ctrs      = pos["contracts"]

    g   = _gross(dir_, ep, exit_px, ctrs)
    fee = _fees(ep, exit_px, ctrs)
    net = g - fee

    portfolio += net
    state["trades"].append({
        "dir":         dir_,
        "entry_px":    ep,
        "exit_px":     round(exit_px, 4),
        "contracts":   ctrs,
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
    return portfolio


def run_cycle() -> dict[str, Any]:
    """
    Run one paper-trading cycle. Safe to call every minute.

    Fast path (every call):
      1. Fetch live price (single API call)
      2. Check open position SL/TP against live price
      3. Append equity snapshot

    Slow path (only when a new 30m bar has closed):
      4. Fetch 150× 30m + 50× 6h candles
      5. Compute GRACE signals
      6. Open/flip position on fresh signal
    """
    client   = CoinbaseClient()
    state    = store.load()
    ts_now   = int(time.time())
    result: dict[str, Any] = {"ts": ts_now, "action": "none", "detail": ""}

    # ── Fast path: live price ─────────────────────────────────────────────────
    try:
        sol_price = float(sum(client.get_best_bid_ask(PRODUCT_ID)) / 2)
    except Exception as exc:
        log.warning("Price fetch failed: %s", exc)
        result.update(action="error", detail=f"price fetch failed: {exc}")
        state.update(last_cycle_ts=ts_now, last_cycle_result=result)
        store.save(state)
        return result

    state["sol_price"] = round(sol_price, 4)
    if state.get("sol_price_at_start") is None:
        state["sol_price_at_start"] = sol_price
        state["sol_start_ts"]       = ts_now

    portfolio = state["portfolio_usd"]
    pos       = state.get("position")

    # ── Fast path: SL/TP check ────────────────────────────────────────────────
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
            portfolio = _close_position(state, exit_px, reason, ts_now)
            pos = None
            result.update(
                action=f"close_{reason.lower()}",
                detail=(f"Closed {dir_} @ {exit_px:.2f} ({reason}), "
                        f"net ${state['trades'][-1]['net_pnl']:+.2f}"),
            )
            log.info(result["detail"])

    # ── Fast path: equity snapshot ────────────────────────────────────────────
    unreal = 0.0
    if state.get("position"):
        p2     = state["position"]
        unreal = _gross(p2["dir"], p2["entry_px"], sol_price, p2["contracts"])
    equity_now = round(portfolio + unreal, 2)
    state["equity_history"].append({"ts": ts_now, "usd": equity_now})
    state["equity_history"] = state["equity_history"][-2016:]   # ~42 days at 30m

    # ── Slow path: only on new 30m bar ────────────────────────────────────────
    new_bar_ts = _current_bar_boundary(ts_now)
    if new_bar_ts <= state.get("last_bar_ts", 0):
        # Same bar as last full cycle — skip candle fetch
        state["last_cycle_ts"]     = ts_now
        state["last_cycle_result"] = result
        store.save(state)
        return result

    try:
        df30 = _fetch_candles(client, "THIRTY_MINUTE", 1800,  150)
        df6h = _fetch_candles(client, "SIX_HOUR",      21600,  50)
    except Exception as exc:
        log.error("Candle fetch failed: %s", exc)
        result.update(action="error", detail=str(exc))
        state.update(last_cycle_ts=ts_now, last_cycle_result=result)
        store.save(state)
        return result

    if df30.empty or df6h.empty or len(df30) < 60:
        result.update(action="error", detail="insufficient candle data")
        state.update(last_cycle_ts=ts_now, last_cycle_result=result)
        store.save(state)
        return result

    sigs, atr_arr, adx_s = _compute_signals(df30, df6h)

    latest_bar_ts = int(df30["start"].iloc[-1])

    # Find the most recent signal on any bar newer than last_bar_ts.
    # Checking only sigs[-1] misses signals that fired hours ago when the
    # cron was not running every minute (e.g. after a manual SL injection,
    # after a restart, or after a multi-bar gap in processing).
    prev_bar_ts   = state.get("last_bar_ts", 0)
    bar_ts_arr    = df30["start"].values
    new_mask      = bar_ts_arr > prev_bar_ts
    new_indices   = np.where(new_mask)[0]

    last_sig    = 0
    last_atr    = float(atr_arr[-1])
    if len(new_indices) > 0:
        new_sigs  = sigs[new_mask]
        nonzero   = np.where(new_sigs != 0)[0]
        if len(nonzero) > 0:
            pick     = new_indices[nonzero[-1]]   # most recent signal bar
            last_sig = int(sigs[pick])
            last_atr = float(atr_arr[pick])

    state["indicator_state"] = _indicator_snapshot(df30, df6h, sigs, adx_s)
    state["last_bar_ts"]     = latest_bar_ts

    # ── Slow path: new signal → open/flip position ────────────────────────────
    if last_sig != 0 and result.get("action") == "none":
        new_dir = "LONG" if last_sig > 0 else "SHORT"
        cur_pos = state.get("position")

        if cur_pos and cur_pos["dir"] == new_dir:
            result.setdefault("action", "hold")
        elif portfolio >= 50:
            if cur_pos:
                portfolio = _close_position(state, sol_price, "FLIP", ts_now)

            ctrs = _num_contracts(portfolio, sol_price)
            if new_dir == "LONG":
                sl_ = sol_price - SL_MULT * last_atr
                tp_ = sol_price + TP_MULT * last_atr
            else:
                sl_ = sol_price + SL_MULT * last_atr
                tp_ = sol_price - TP_MULT * last_atr

            if (new_dir == "LONG" and sl_ < sol_price) or \
               (new_dir == "SHORT" and sl_ > sol_price):
                state["position"] = {
                    "dir":       new_dir,
                    "entry_px":  round(sol_price, 4),
                    "sl":        round(sl_, 4),
                    "tp":        round(tp_, 4),
                    "contracts": ctrs,
                    "entry_ts":  ts_now,
                }
                state["portfolio_usd"] = round(portfolio, 2)
                result.update(
                    action=f"open_{new_dir.lower()}",
                    detail=(f"Opened {new_dir} {ctrs}c @ {sol_price:.2f} "
                            f"| SL {sl_:.2f} | TP {tp_:.2f}"),
                )
                log.info(result["detail"])

    state["portfolio_usd"]     = round(portfolio, 2)
    state["last_cycle_ts"]     = ts_now
    state["last_cycle_result"] = result
    store.save(state)
    return result
