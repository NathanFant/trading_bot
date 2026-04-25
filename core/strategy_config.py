"""
GRACE v2 strategy constants — single source of truth.

Both the live trading engine (core/mock_trader.py) and the status API
(api/mock_status.py) import from here so the frontend always reflects
the parameters the algorithm is actually running with.
"""

# Signal filters
ADX_MIN    = 25.0   # minimum ADX for a valid entry
SL_MULT    = 2.5    # stop-loss  = entry ± SL_MULT  × ATR₁₄
TP_MULT    = 4.0    # take-profit = entry ± TP_MULT × ATR₁₄
REG_EMA_P  = 50     # regime filter: EMA period applied to 6h closes

# ATR volatility gate
ATR_MA_P   = 30     # period for ATR moving average
ATR_LOW    = 0.5    # ATR must be > ATR_MA × ATR_LOW
ATR_HIGH   = 2.5    # ATR must be < ATR_MA × ATR_HIGH

# Serialisable dict for the status API response
def as_dict() -> dict:
    return {
        "adx_min":   ADX_MIN,
        "sl_mult":   SL_MULT,
        "tp_mult":   TP_MULT,
        "reg_ema_p": REG_EMA_P,
        "atr_ma_p":  ATR_MA_P,
        "atr_low":   ATR_LOW,
        "atr_high":  ATR_HIGH,
    }
