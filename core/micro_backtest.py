"""
Micro-trading backtest engine for Coinbase perpetual futures.

Accurate fee model:
  - Taker: 0.1% of notional per side
  - NFA:   $0.15 per contract per side
  - Liquidation: 0.10% fee, triggered at 20% adverse move (5x leverage)

Contract spec (SOL perp — SLP-20DEC30-CDE):
  - 5 SOL per contract
  - 5x leverage → liquidation at 20% adverse price move

Key design: strategies emit EVENTS only (signal fires on the bar where the
condition first becomes true, NOT carried forward). After an SL/LIQ exit the
bot stays flat until the next fresh signal. This avoids the fee death-spiral
from repeated re-entries after losses.

Position sizing: fixed fraction of current portfolio deployed as margin per trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# ── Constants ─────────────────────────────────────────────────────────────────

CONTRACT_SIZE  = 5.0    # SOL per contract
LEVERAGE       = 5.0    # intraday max
TAKER_FEE_PCT  = 0.001  # 0.1% per side (taker)
NFA_FEE        = 0.15   # USD per contract per side
LIQ_MOVE_PCT   = 1.0 / LEVERAGE   # 20% adverse → liquidation
LIQ_FEE_PCT    = 0.001  # 0.1% of notional on liquidation
MARGIN_FRACTION = 0.35  # fraction of portfolio as margin per trade

DATA_DIR = Path(__file__).parent.parent / "data" / "perp_candles"


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class Trade:
    direction:   str    # 'LONG' | 'SHORT'
    entry_bar:   int
    exit_bar:    int
    entry_price: float
    exit_price:  float
    contracts:   int
    gross_pnl:   float
    fees:        float
    net_pnl:     float
    exit_reason: str    # 'TP' | 'SL' | 'LIQ' | 'FLIP' | 'END'


@dataclass
class StrategyResult:
    name:             str
    timeframe:        str
    final_usd:        float
    total_return_pct: float
    sharpe:           float
    max_drawdown_pct: float
    num_trades:       int
    win_rate:         float
    profit_factor:    float
    avg_trade_pnl:    float
    total_fees:       float
    trades:           list[Trade] = field(default_factory=list)
    equity:           list[float] = field(default_factory=list)


# ── Fee / PnL helpers ─────────────────────────────────────────────────────────

def _fees(entry_px: float, exit_px: float, contracts: int,
          fee_pct: float = TAKER_FEE_PCT) -> float:
    return (fee_pct * contracts * CONTRACT_SIZE * entry_px + NFA_FEE * contracts +
            fee_pct * contracts * CONTRACT_SIZE * exit_px  + NFA_FEE * contracts)


def _liq_fees(price: float, contracts: int) -> float:
    return LIQ_FEE_PCT * contracts * CONTRACT_SIZE * price


def _gross(direction: str, entry: float, exit_px: float, contracts: int) -> float:
    delta = exit_px - entry if direction == "LONG" else entry - exit_px
    return delta * contracts * CONTRACT_SIZE


def _liq_px(direction: str, entry: float) -> float:
    if direction == "LONG":
        return entry * (1.0 - LIQ_MOVE_PCT)
    return entry * (1.0 + LIQ_MOVE_PCT)


def _num_contracts(portfolio: float, price: float,
                   fraction: float = MARGIN_FRACTION) -> int:
    margin_per = (price * CONTRACT_SIZE) / LEVERAGE
    return max(1, int((portfolio * fraction) / margin_per))


# ── Indicators ────────────────────────────────────────────────────────────────

def ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()


def atr(df: pd.DataFrame, p: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l,
                    (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()


def rsi(s: pd.Series, p: int = 14) -> pd.Series:
    d = s.diff()
    gain = d.clip(lower=0).ewm(span=p, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(span=p, adjust=False).mean()
    rs   = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(s: pd.Series, f: int = 12, sl: int = 26,
         sig: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    line   = ema(s, f) - ema(s, sl)
    signal = ema(line, sig)
    return line, signal, line - signal


def bbands(s: pd.Series, p: int = 20, k: float = 2.0):
    mid = s.rolling(p).mean()
    std = s.rolling(p).std()
    return mid - k * std, mid, mid + k * std


def adx(df: pd.DataFrame, p: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    up, down = h - h.shift(), l.shift() - l
    dm_p = up.where((up > down) & (up > 0), 0.0)
    dm_m = down.where((down > up) & (down > 0), 0.0)
    atr_s = atr(df, p)
    di_p = 100 * ema(dm_p, p) / atr_s
    di_m = 100 * ema(dm_m, p) / atr_s
    dx   = (100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, np.nan))
    return ema(dx, p)


def supertrend(df: pd.DataFrame, p: int = 10, factor: float = 3.0) -> pd.Series:
    """Returns +1 (uptrend) / -1 (downtrend) aligned to df index."""
    c   = df["close"]
    atr_s = atr(df, p)
    hl2 = (df["high"] + df["low"]) / 2
    ub  = hl2 + factor * atr_s
    lb  = hl2 - factor * atr_s

    trend  = pd.Series(1, index=df.index)
    final_ub = ub.copy()
    final_lb = lb.copy()

    for i in range(1, len(df)):
        # Upper band
        final_ub.iloc[i] = (ub.iloc[i] if ub.iloc[i] < final_ub.iloc[i-1]
                            or c.iloc[i-1] > final_ub.iloc[i-1]
                            else final_ub.iloc[i-1])
        # Lower band
        final_lb.iloc[i] = (lb.iloc[i] if lb.iloc[i] > final_lb.iloc[i-1]
                            or c.iloc[i-1] < final_lb.iloc[i-1]
                            else final_lb.iloc[i-1])
        # Trend
        if trend.iloc[i-1] == 1:
            trend.iloc[i] = 1 if c.iloc[i] >= final_lb.iloc[i] else -1
        else:
            trend.iloc[i] = -1 if c.iloc[i] <= final_ub.iloc[i] else 1

    return trend


# ── Backtest loop ─────────────────────────────────────────────────────────────

def _run_loop(df: pd.DataFrame,
              signals: np.ndarray,    # +1, -1, 0  — event signals only
              atr_arr: np.ndarray,    # ATR per bar (for SL/TP sizing at entry)
              sl_mult: float,
              tp_mult: float,
              starting_cash: float) -> tuple[list[Trade], list[float]]:
    """
    Signals must be EVENTS: non-zero only on bars where the condition newly
    becomes true. The loop holds the position until SL/TP/LIQ, then waits
    flat for the next event signal.
    """
    portfolio = starting_cash
    trades: list[Trade] = []
    equity: list[float] = []

    pos_dir:   str | None = None
    pos_entry: float = 0.0
    pos_sl:    float = 0.0
    pos_tp:    float = 0.0
    pos_ctrs:  int   = 0
    pos_bar:   int   = 0

    opens  = df["open"].to_numpy()
    highs  = df["high"].to_numpy()
    lows   = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    n      = len(df)

    for i in range(n):
        if pos_dir is not None:
            # ── Liquidation check ────────────────────────────────────────────
            lq = _liq_px(pos_dir, pos_entry)
            if (pos_dir == "LONG" and lows[i] <= lq) or \
               (pos_dir == "SHORT" and highs[i] >= lq):
                g   = _gross(pos_dir, pos_entry, lq, pos_ctrs)
                fee = _fees(pos_entry, lq, pos_ctrs) + _liq_fees(lq, pos_ctrs)
                net = g - fee
                portfolio = max(0.0, portfolio + net)
                trades.append(Trade(pos_dir, pos_bar, i, pos_entry, lq,
                                    pos_ctrs, g, fee, net, "LIQ"))
                pos_dir = None
                equity.append(portfolio)
                continue

            # ── SL / TP check ────────────────────────────────────────────────
            sl_hit = (pos_dir == "LONG"  and lows[i]  <= pos_sl) or \
                     (pos_dir == "SHORT" and highs[i] >= pos_sl)
            tp_hit = (pos_dir == "LONG"  and highs[i] >= pos_tp) or \
                     (pos_dir == "SHORT" and lows[i]  <= pos_tp)

            if sl_hit or tp_hit:
                if sl_hit and tp_hit:
                    exit_px, reason = pos_sl, "SL"   # assume worst case
                elif sl_hit:
                    exit_px, reason = pos_sl, "SL"
                else:
                    exit_px, reason = pos_tp, "TP"

                g   = _gross(pos_dir, pos_entry, exit_px, pos_ctrs)
                fee = _fees(pos_entry, exit_px, pos_ctrs)
                net = g - fee
                portfolio += net
                trades.append(Trade(pos_dir, pos_bar, i, pos_entry, exit_px,
                                    pos_ctrs, g, fee, net, reason))
                pos_dir = None
                equity.append(portfolio)
                continue

        # ── New signal (entry on same bar open, signal is already for this bar) ─
        sig = int(signals[i])
        if sig == 0:
            equity.append(portfolio)
            continue

        new_dir = "LONG" if sig > 0 else "SHORT"

        if pos_dir == new_dir:
            equity.append(portfolio)
            continue

        if pos_dir is not None:
            # Flip: close at this bar's open
            g   = _gross(pos_dir, pos_entry, opens[i], pos_ctrs)
            fee = _fees(pos_entry, opens[i], pos_ctrs)
            net = g - fee
            portfolio += net
            trades.append(Trade(pos_dir, pos_bar, i, pos_entry, opens[i],
                                pos_ctrs, g, fee, net, "FLIP"))
            pos_dir = None

        if portfolio < 50:
            equity.append(portfolio)
            continue

        # Open new position at this bar's open
        entry_px = opens[i]
        a        = atr_arr[i]
        ctrs     = _num_contracts(portfolio, entry_px)

        if new_dir == "LONG":
            sl = entry_px - sl_mult * a
            tp = entry_px + tp_mult * a
        else:
            sl = entry_px + sl_mult * a
            tp = entry_px - tp_mult * a

        # Sanity: SL must be on the loss side
        if (new_dir == "LONG" and sl >= entry_px) or \
           (new_dir == "SHORT" and sl <= entry_px):
            equity.append(portfolio)
            continue

        pos_dir   = new_dir
        pos_entry = entry_px
        pos_sl    = sl
        pos_tp    = tp
        pos_ctrs  = ctrs
        pos_bar   = i
        equity.append(portfolio)

    # Close any open position at final bar
    if pos_dir is not None:
        exit_px = closes[-1]
        g   = _gross(pos_dir, pos_entry, exit_px, pos_ctrs)
        fee = _fees(pos_entry, exit_px, pos_ctrs)
        net = g - fee
        portfolio += net
        trades.append(Trade(pos_dir, pos_bar, n-1, pos_entry, exit_px,
                            pos_ctrs, g, fee, net, "END"))

    return trades, equity


def _summarise(name: str, tf: str, starting: float,
               trades: list[Trade], equity: list[float]) -> StrategyResult:
    final = equity[-1] if equity else starting
    ret   = (final - starting) / starting * 100

    if len(trades) > 1:
        nets   = np.array([t.net_pnl for t in trades]) / starting
        # Annualise assuming uniform distribution across 90 days
        ann    = math.sqrt(365 * 24)
        sharpe = nets.mean() / (nets.std(ddof=1) + 1e-9) * ann
    else:
        sharpe = 0.0

    eq   = np.array(equity)
    peak = np.maximum.accumulate(eq)
    dd   = (peak - eq) / (peak + 1e-9) * 100
    maxdd = float(dd.max())

    wins   = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    wr     = len(wins) / len(trades) * 100 if trades else 0.0
    gw     = sum(t.net_pnl for t in wins)
    gl     = abs(sum(t.net_pnl for t in losses))
    pf     = gw / gl if gl > 0 else float("inf")
    avg    = sum(t.net_pnl for t in trades) / len(trades) if trades else 0.0
    fees   = sum(t.fees for t in trades)

    return StrategyResult(name, tf, final, ret, sharpe, maxdd, len(trades),
                          wr, pf, avg, fees, trades, equity)


def run_micro_backtest(df: pd.DataFrame, name: str, timeframe: str,
                       signals: np.ndarray, atr_arr: np.ndarray,
                       sl_mult: float, tp_mult: float,
                       starting_cash: float = 1000.0) -> StrategyResult:
    """
    Signals are computed using bar i's OHLCV (known after bar i closes).
    Entry executes at bar i+1's open — no look-ahead bias.
    ATR from bar i (signal bar) is used to size SL/TP at the bar i+1 entry.
    """
    # Shift by 1: signal from bar i → entry on bar i+1
    shifted_sigs = np.roll(signals, 1).astype(float)
    shifted_sigs[0] = 0.0
    shifted_atr = np.roll(atr_arr, 1)
    shifted_atr[0] = atr_arr[0] if len(atr_arr) > 0 else 1.0

    trades, equity = _run_loop(df, shifted_sigs, shifted_atr, sl_mult, tp_mult, starting_cash)
    return _summarise(name, timeframe, starting_cash, trades, equity)


# ── Strategies ────────────────────────────────────────────────────────────────
# Each returns (signals, atr_arr, sl_mult, tp_mult).
# Signals are EVENT-only: non-zero ONLY on the bar where condition first fires.
# No carry-forward. Position held by loop until SL/TP/flip.

def strat_ema_adx(df: pd.DataFrame,
                  fast: int = 9, slow: int = 21, trend_p: int = 55,
                  adx_p: int = 14, adx_min: float = 20.0,
                  sl_mult: float = 1.5, tp_mult: float = 4.0):
    c      = df["close"]
    ef     = ema(c, fast)
    es     = ema(c, slow)
    et     = ema(c, trend_p)
    adx_s  = adx(df, adx_p)
    atr_s  = atr(df, 14).to_numpy()

    cross_up   = (ef > es) & (ef.shift(1) <= es.shift(1))
    cross_down = (ef < es) & (ef.shift(1) >= es.shift(1))
    strong     = adx_s >= adx_min
    up_trend   = es > et
    dn_trend   = es < et

    sigs = np.zeros(len(df))
    sigs[cross_up   & strong & up_trend] = +1
    sigs[cross_down & strong & dn_trend] = -1
    return sigs, atr_s, sl_mult, tp_mult


def strat_supertrend(df: pd.DataFrame,
                     st_period: int = 10, st_factor: float = 3.0,
                     sl_mult: float = 1.5, tp_mult: float = 3.5):
    c     = df["close"]
    st    = supertrend(df, st_period, st_factor)
    atr_s = atr(df, 14).to_numpy()

    # Signal fires on trend CHANGE (event), not persistent state
    trend_flip_up   = (st == 1)  & (st.shift(1) == -1)
    trend_flip_down = (st == -1) & (st.shift(1) == 1)

    sigs = np.zeros(len(df))
    sigs[trend_flip_up]   = +1
    sigs[trend_flip_down] = -1
    return sigs, atr_s, sl_mult, tp_mult


def strat_macd_cross(df: pd.DataFrame,
                     fast: int = 12, slow: int = 26, sig_p: int = 9,
                     trend_p: int = 50, adx_p: int = 14, adx_min: float = 18.0,
                     sl_mult: float = 1.5, tp_mult: float = 3.5):
    c      = df["close"]
    _, _, hist = macd(c, fast, slow, sig_p)
    et     = ema(c, trend_p)
    adx_s  = adx(df, adx_p)
    atr_s  = atr(df, 14).to_numpy()

    hist_cross_up   = (hist > 0) & (hist.shift(1) <= 0)
    hist_cross_down = (hist < 0) & (hist.shift(1) >= 0)
    above_trend     = c > et
    below_trend     = c < et
    strong          = adx_s >= adx_min

    sigs = np.zeros(len(df))
    sigs[hist_cross_up   & above_trend & strong] = +1
    sigs[hist_cross_down & below_trend & strong] = -1
    return sigs, atr_s, sl_mult, tp_mult


def strat_donchian_vol(df: pd.DataFrame,
                       period: int = 20, vol_mult: float = 1.3,
                       sl_mult: float = 1.2, tp_mult: float = 3.0):
    """Donchian channel breakout confirmed by above-average volume."""
    c     = df["close"]
    v     = df["volume"]
    h_max = df["high"].shift(1).rolling(period).max()
    l_min = df["low"].shift(1).rolling(period).min()
    vol_avg = v.rolling(period).mean()
    atr_s = atr(df, 14).to_numpy()

    high_break = (c > h_max) & (v > vol_mult * vol_avg)
    low_break  = (c < l_min) & (v > vol_mult * vol_avg)

    sigs = np.zeros(len(df))
    sigs[high_break] = +1
    sigs[low_break]  = -1
    return sigs, atr_s, sl_mult, tp_mult


def strat_bb_squeeze(df: pd.DataFrame,
                     bb_p: int = 20, bb_k: float = 2.0,
                     kc_mult: float = 1.5,
                     sl_mult: float = 1.5, tp_mult: float = 3.5):
    """Bollinger Band squeeze (BB inside Keltner Channel) + breakout momentum."""
    c     = df["close"]
    lb, lm, ub = bbands(c, bb_p, bb_k)
    atr_s = atr(df, bb_p)
    kc_u  = lm + kc_mult * atr_s
    kc_l  = lm - kc_mult * atr_s

    in_squeeze   = (lb > kc_l) & (ub < kc_u)
    was_squeeze  = in_squeeze.shift(1).fillna(False)
    squeeze_exit = was_squeeze & ~in_squeeze

    mom = c - c.rolling(bb_p).mean()
    sigs = np.zeros(len(df))
    sigs[squeeze_exit & (mom > 0)] = +1
    sigs[squeeze_exit & (mom < 0)] = -1
    return sigs, atr_s.to_numpy(), sl_mult, tp_mult


def strat_rsi_mom(df: pd.DataFrame,
                  rsi_p: int = 7, trend_p: int = 50,
                  entry_long: float = 55.0, entry_short: float = 45.0,
                  sl_mult: float = 1.2, tp_mult: float = 3.0):
    """RSI momentum cross in direction of EMA trend (event-based)."""
    c     = df["close"]
    r     = rsi(c, rsi_p)
    et    = ema(c, trend_p)
    atr_s = atr(df, 14).to_numpy()

    in_uptrend  = c > et
    in_dntrend  = c < et

    rsi_cross_up   = (r >= entry_long)  & (r.shift(1) < entry_long)
    rsi_cross_down = (r <= entry_short) & (r.shift(1) > entry_short)

    sigs = np.zeros(len(df))
    sigs[rsi_cross_up   & in_uptrend]  = +1
    sigs[rsi_cross_down & in_dntrend]  = -1
    return sigs, atr_s, sl_mult, tp_mult


def strat_trend_pullback(df: pd.DataFrame,
                         trend_p: int = 100, fast_p: int = 8, slow_p: int = 21,
                         rsi_p: int = 7,
                         rsi_long_entry: float = 45.0,   # RSI recovers above this after pullback
                         rsi_short_entry: float = 55.0,  # RSI drops below this after rally
                         sl_mult: float = 1.5, tp_mult: float = 4.0):
    """
    Trend-pullback entry:
    Long  : price > EMA(100) [uptrend] AND RSI pulls back below 40 THEN crosses above 45
    Short : price < EMA(100) [downtrend] AND RSI rallies above 60 THEN crosses below 55
    Entry on the RSI threshold cross, giving a low-risk pullback entry within the trend.
    """
    c     = df["close"]
    et    = ema(c, trend_p)
    ef    = ema(c, fast_p)
    es    = ema(c, slow_p)
    r     = rsi(c, rsi_p)
    atr_s = atr(df, 14).to_numpy()

    uptrend  = (c > et) & (ef > es)
    dntrend  = (c < et) & (ef < es)

    # RSI cross events
    rsi_recovery = (r >= rsi_long_entry)  & (r.shift(1) < rsi_long_entry)
    rsi_rollover = (r <= rsi_short_entry) & (r.shift(1) > rsi_short_entry)

    sigs = np.zeros(len(df))
    sigs[rsi_recovery & uptrend]  = +1
    sigs[rsi_rollover & dntrend]  = -1
    return sigs, atr_s, sl_mult, tp_mult


def strat_ema_adx_regime(df: pd.DataFrame, df_regime: pd.DataFrame,
                         fast: int = 9, slow: int = 21, trend_p: int = 55,
                         adx_p: int = 14, adx_min: float = 18.0,
                         regime_ema: int = 21,
                         sl_mult: float = 1.5, tp_mult: float = 4.0):
    """
    EMA-ADX with 6h regime filter: only trade in the direction of the
    6h EMA(21) trend. Avoids counter-trend trades during strong directional moves.
    """
    # Build regime direction aligned to signal bars
    c_reg  = df_regime["close"]
    e_reg  = ema(c_reg, regime_ema)
    reg_dir = (c_reg > e_reg).astype(int).to_numpy()  # 1=bull, 0=bear
    ts_reg  = df_regime["start"].to_numpy()
    ts_sig  = df["start"].to_numpy()

    aligned = np.zeros(len(df), dtype=int)
    j = 0
    for i, ts in enumerate(ts_sig):
        while j + 1 < len(ts_reg) and ts_reg[j+1] <= ts:
            j += 1
        aligned[i] = reg_dir[j]

    c      = df["close"]
    ef     = ema(c, fast)
    es_    = ema(c, slow)
    et     = ema(c, trend_p)
    adx_s  = adx(df, adx_p)
    atr_s  = atr(df, 14).to_numpy()

    cross_up   = (ef > es_) & (ef.shift(1) <= es_.shift(1))
    cross_down = (ef < es_) & (ef.shift(1) >= es_.shift(1))
    strong     = adx_s >= adx_min
    up_trend   = es_ > et
    dn_trend   = es_ < et

    sigs = np.zeros(len(df))
    sigs[cross_up   & strong & up_trend & (aligned == 1)] = +1
    sigs[cross_down & strong & dn_trend & (aligned == 0)] = -1
    return sigs, atr_s, sl_mult, tp_mult


def strat_keltner_breakout(df: pd.DataFrame,
                            ema_p: int = 20, atr_p: int = 14,
                            mult_entry: float = 2.0, mult_ema: int = 50,
                            sl_mult: float = 1.5, tp_mult: float = 3.5):
    """
    Keltner Channel breakout: close outside KC triggers entry in breakout direction.
    Confirmed by EMA(50) trend alignment.
    """
    c      = df["close"]
    mid    = ema(c, ema_p)
    atr_s  = atr(df, atr_p)
    kc_u   = mid + mult_entry * atr_s
    kc_l   = mid - mult_entry * atr_s
    et     = ema(c, mult_ema)

    break_up   = (c > kc_u) & (c.shift(1) <= kc_u.shift(1)) & (c > et)
    break_down = (c < kc_l) & (c.shift(1) >= kc_l.shift(1)) & (c < et)

    sigs = np.zeros(len(df))
    sigs[break_up]   = +1
    sigs[break_down] = -1
    return sigs, atr_s.to_numpy(), sl_mult, tp_mult


def strat_mtf_ema(df_signal: pd.DataFrame, df_trend: pd.DataFrame,
                  fast: int = 9, slow: int = 21, trend_p: int = 50,
                  sl_mult: float = 1.5, tp_mult: float = 4.0):
    """Higher-TF EMA trend + lower-TF EMA cross event entry."""
    c_trend = df_trend["close"]
    et      = ema(c_trend, trend_p)
    trend_dir = (c_trend > et).astype(int).to_numpy()  # 1=up, 0=down
    ts_trend  = df_trend["start"].to_numpy()
    ts_sig    = df_signal["start"].to_numpy()

    # Align trend to signal bars (last known trend at each signal bar)
    aligned = np.zeros(len(df_signal), dtype=int)
    j = 0
    for i, ts in enumerate(ts_sig):
        while j + 1 < len(ts_trend) and ts_trend[j+1] <= ts:
            j += 1
        aligned[i] = trend_dir[j]

    c  = df_signal["close"]
    ef = ema(c, fast)
    es = ema(c, slow)
    a  = atr(df_signal, 14).to_numpy()

    cross_up   = (ef > es) & (ef.shift(1) <= es.shift(1))
    cross_down = (ef < es) & (ef.shift(1) >= es.shift(1))

    sigs = np.zeros(len(df_signal))
    sigs[cross_up   & (aligned == 1)] = +1
    sigs[cross_down & (aligned == 0)] = -1
    return sigs, a, sl_mult, tp_mult


def strat_ema_adx_regime_v2(df: pd.DataFrame, df_regime: pd.DataFrame,
                              fast: int = 9, slow: int = 21, trend_p: int = 55,
                              adx_p: int = 14, adx_min: float = 18.0,
                              regime_ema: int = 21,
                              vol_ma_p: int = 20, vol_mult: float = 1.2,
                              roc_bars: int = 3,
                              atr_ma_p: int = 30, atr_low: float = 0.5, atr_high: float = 2.5,
                              sl_mult: float = 1.5, tp_mult: float = 4.0):
    """
    EMA-ADX+Regime V2: adds three selectivity filters on top of the base strategy.
      - Volume surge   : volume > vol_ma(20) × 1.2  (confirms genuine breakout)
      - 3-bar ROC      : price already moving in signal direction
      - ATR health     : market is neither too quiet nor too volatile
    Designed to raise win rate ~2% while keeping trade frequency viable.
    """
    c = df["close"]
    v = df["volume"]

    ef    = ema(c, fast)
    es_   = ema(c, slow)
    et    = ema(c, trend_p)
    adx_s = adx(df, adx_p)
    atr_s = atr(df, 14)

    # Volume surge
    vol_ma  = v.rolling(vol_ma_p).mean()
    vol_ok  = v > vol_ma * vol_mult

    # 3-bar rate-of-change confirmation
    roc3      = c / c.shift(roc_bars) - 1
    roc_long  = roc3 > 0
    roc_short = roc3 < 0

    # ATR health: avoid dead markets and volatility spikes
    atr_ma = atr_s.rolling(atr_ma_p).mean()
    atr_ok = (atr_s > atr_ma * atr_low) & (atr_s < atr_ma * atr_high)

    # 6h regime
    c_reg   = df_regime["close"]
    e_reg   = ema(c_reg, regime_ema)
    reg_dir = (c_reg > e_reg).astype(int).to_numpy()
    ts_reg  = df_regime["start"].to_numpy()
    ts_sig  = df["start"].to_numpy()

    aligned = np.zeros(len(df), dtype=int)
    j = 0
    for i, ts in enumerate(ts_sig):
        while j + 1 < len(ts_reg) and ts_reg[j + 1] <= ts:
            j += 1
        aligned[i] = reg_dir[j]

    cross_up   = (ef > es_) & (ef.shift(1) <= es_.shift(1))
    cross_down = (ef < es_) & (ef.shift(1) >= es_.shift(1))
    strong     = adx_s >= adx_min
    up_trend   = es_ > et
    dn_trend   = es_ < et

    sigs = np.zeros(len(df))
    sigs[cross_up   & strong & up_trend & (aligned == 1) & vol_ok & roc_long  & atr_ok] = +1
    sigs[cross_down & strong & dn_trend & (aligned == 0) & vol_ok & roc_short & atr_ok] = -1
    return sigs, atr_s.to_numpy(), sl_mult, tp_mult


# ── Loader ────────────────────────────────────────────────────────────────────

def load_candles(asset: str, timeframe: str) -> pd.DataFrame:
    path = DATA_DIR / f"{asset}_{timeframe}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No data: {path}. Run scripts/fetch_perp_data.py first.")
    df = pd.read_csv(path)
    df["start"] = df["start"].astype(int)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    return df.sort_values("start").reset_index(drop=True)


def resample_to_10m(df5: pd.DataFrame) -> pd.DataFrame:
    df = df5.copy()
    df["bucket"] = (df["start"] // 600) * 600
    out = df.groupby("bucket").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index().rename(columns={"bucket": "start"})
    return out.sort_values("start").reset_index(drop=True)
