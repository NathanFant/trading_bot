"""
Backtesting engine + Monte Carlo simulation.

Data sources:
  - FGI history: Alternative.me (up to ~5 years)
  - Price data:  CoinGecko free API (up to 365 days, any supported coin)

Monte Carlo: runs 200+ simulations with randomised strategy parameters,
reports the distribution of outcomes so you can pick robust thresholds.
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import requests

from fgi import FGIReading, fetch_history
from signals import BayesianUpdater, Signal, compute_signal

logger = logging.getLogger(__name__)


# ── Data loading ──────────────────────────────────────────────────────────────

@dataclass
class DailyBar:
    date_ts: int    # unix seconds (midnight UTC approx.)
    fgi: int
    price: float    # BTC/USD close


COINGECKO_IDS = {
    "BTC-USD": "bitcoin",
    "SOL-USD": "solana",
    "ETH-USD": "ethereum",
}

# Binance symbol mapping (SOL-USD → SOLUSDT, etc.)
BINANCE_SYMBOLS = {
    "BTC-USD": "BTCUSDT",
    "SOL-USD": "SOLUSDT",
    "ETH-USD": "ETHUSDT",
}

# Estimated Robinhood spread per coin (percentage, one-way).
# BTC is most liquid; alts carry wider spreads.
SPREAD_PCT = {
    "BTC-USD": 0.005,   # ~0.5%
    "SOL-USD": 0.010,   # ~1.0%
    "ETH-USD": 0.007,   # ~0.7%
}


_PRICE_CACHE_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent)) / ".price_cache"


def _price_cache_path(symbol: str, days: int) -> Path:
    return _PRICE_CACHE_DIR / f"{symbol.replace('-', '_')}_{days}d.json"


def _fetch_prices_binance(binance_symbol: str, days: int, base: str) -> dict[int, float]:
    """Fetch daily closes from Binance (or Binance.US). Returns {} on any error."""
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86400 * 1000
    chunk_ms = 999 * 86400 * 1000
    out: dict[int, float] = {}
    t = start_ms
    try:
        while t < now_ms:
            end_t = min(t + chunk_ms, now_ms)
            url = (f"{base}/api/v3/klines?symbol={binance_symbol}&interval=1d"
                   f"&startTime={t}&endTime={end_t}&limit=1000")
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            for c in resp.json():
                bucket = int(c[0] // 1000 // 86400) * 86400
                out[bucket] = float(c[4])
            t = end_t + 1
        logger.info("Fetched %d price bars from %s", len(out), base)
        return out
    except Exception as exc:
        logger.warning("%s failed: %s", base, exc)
        return {}


def _fetch_prices_kraken(pair: str, days: int) -> dict[int, float]:
    """Fetch daily closes from Kraken public OHLC. Returns {} on error.
    Kraken caps at 720 bars/call; we paginate with the `since` param."""
    out: dict[int, float] = {}
    since = int(time.time()) - days * 86400
    try:
        while True:
            url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440&since={since}"
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            body = resp.json()
            if body.get("error"):
                raise RuntimeError(f"Kraken error: {body['error']}")
            result = body["result"]
            data = result.get(pair) or next(iter(v for k, v in result.items() if k != "last"), [])
            if not data:
                break
            for row in data:
                bucket = int(row[0] // 86400) * 86400
                out[bucket] = float(row[4])  # close
            last = int(result.get("last", 0))
            if last <= since or not data:
                break
            since = last
        logger.info("Fetched %d price bars from Kraken", len(out))
        return out
    except Exception as exc:
        logger.warning("Kraken failed: %s", exc)
        return {}


def _fetch_prices_coingecko(coin_id: str, days: int) -> dict[int, float]:
    """Fetch daily closes from CoinGecko free tier (≤365 days). Returns {} on error."""
    try:
        url = (f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
               f"?vs_currency=usd&days={days}")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        out = {int(ts // 1000 // 86400) * 86400: float(p)
               for ts, p in resp.json()["prices"]}
        logger.info("Fetched %d price bars from CoinGecko", len(out))
        return out
    except Exception as exc:
        logger.warning("CoinGecko failed: %s", exc)
        return {}


def _load_price_cache(symbol: str, days: int) -> dict[int, float] | None:
    """Return cached price data if it was fetched today, else None."""
    path = _price_cache_path(symbol, days)
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > 86400:   # stale after 24 h
        return None
    import json as _json
    raw = _json.loads(path.read_text())
    return {int(k): v for k, v in raw.items()}


def _save_price_cache(symbol: str, days: int, data: dict[int, float]) -> None:
    import json as _json
    _PRICE_CACHE_DIR.mkdir(exist_ok=True)
    _price_cache_path(symbol, days).write_text(_json.dumps(data))


def _fetch_prices(symbol: str, days: int = 365) -> dict[int, float]:
    """
    Fetch daily prices from CoinGecko. Returns {day_bucket_ts: price}.

    Results are cached to disk for 24 h to avoid hammering the free-tier
    rate limit across multiple runs in the same session.

    CoinGecko free tier automatically returns daily granularity when
    days > 90, so no interval parameter is needed for long windows.
    For windows > 365 days we split into 365-day /range chunks and
    sleep between requests.
    """
    cached = _load_price_cache(symbol, days)
    if cached:
        logger.info("Using cached %s prices (%d buckets)", symbol, len(cached))
        return cached

    coin_id = COINGECKO_IDS.get(symbol, "bitcoin")
    all_prices: dict[int, float] = {}

    # Price sources tried in order:
    #   1. Binance.US  (US-friendly, free, no auth, 1000 candles/call)
    #   2. Binance.com (non-US, same API)
    #   3. Kraken      (global, free, no auth, 720 candles/call — paginated)
    binance_symbol = BINANCE_SYMBOLS.get(symbol, symbol.replace("-", "").replace("USD", "USDT"))
    kraken_pair = {"BTC-USD": "XBTUSD", "SOL-USD": "SOLUSD", "ETH-USD": "ETHUSD"}.get(symbol, "XBTUSD")

    fetched = _fetch_prices_binance(binance_symbol, days, "https://api.binance.us")
    if not fetched:
        fetched = _fetch_prices_binance(binance_symbol, days, "https://api.binance.com")
    if not fetched:
        fetched = _fetch_prices_kraken(kraken_pair, days)
    if not fetched and days <= 365:
        fetched = _fetch_prices_coingecko(coin_id, days)

    if not fetched:
        raise RuntimeError(f"All price sources failed for {symbol} ({days} days)")
    all_prices.update(fetched)

    if not all_prices:
        raise RuntimeError(f"No price data returned for {symbol}")

    _save_price_cache(symbol, days, all_prices)
    return all_prices


def load_aligned_data(days: int = 365, symbol: str = "BTC-USD") -> list[DailyBar]:
    """Return daily FGI+price bars for the given symbol, aligned by date, oldest-first."""
    logger.info("Fetching %d days of FGI history…", days)
    fgi_history = fetch_history(days)

    logger.info("Fetching %d days of %s price history…", days, symbol)
    price_map = _fetch_prices(symbol, days)

    bars: list[DailyBar] = []
    for reading in fgi_history:
        day_bucket = int(reading.timestamp // 86400) * 86400
        # Accept prices within ±1 day of the FGI timestamp
        price = (
            price_map.get(day_bucket)
            or price_map.get(day_bucket - 86400)
            or price_map.get(day_bucket + 86400)
        )
        if price:
            bars.append(DailyBar(date_ts=day_bucket, fgi=reading.fgi if hasattr(reading, 'fgi') else reading.value, price=price))

    bars.sort(key=lambda b: b.date_ts)
    logger.info("Aligned %d daily bars", len(bars))
    return bars


# ── Single backtest run ───────────────────────────────────────────────────────

@dataclass
class BacktestParams:
    buy_z_threshold: float = -1.5
    sell_z_threshold: float = 1.5
    position_size_pct: float = 0.25   # flat buy fraction (ignored when scale_buy_with_z=True)
    lookback_days: int = 90           # rolling window for z-score
    min_confidence: float = 0.55
    starting_cash: float = 10_000.0
    symbol: str = "BTC-USD"
    spread_pct: float = 0.005         # one-way Robinhood spread cost
    sell_pct: float | None = None     # sell fraction; None means same as position_size_pct

    # Z-score-scaled buy sizing
    scale_buy_with_z: bool = False    # if True, buy fraction grows with fear intensity
    min_buy_pct: float = 0.15         # buy fraction at exactly the threshold z-score
    max_buy_pct: float = 0.60         # buy fraction at max_scale_z or beyond
    max_scale_z: float = 4.0          # z-score magnitude at which max_buy_pct is reached

    # Z-score-scaled sell sizing
    scale_sell_with_z: bool = False   # if True, sell fraction grows with greed intensity
    min_sell_pct: float = 0.30        # sell fraction at exactly the sell threshold z-score
    max_sell_pct: float = 0.90        # sell fraction at max_sell_z or beyond
    max_sell_z: float = 4.0           # z-score magnitude at which max_sell_pct is reached


@dataclass
class BacktestResult:
    params: BacktestParams
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    num_trades: int
    win_rate: float
    final_portfolio_usd: float
    buy_hold_return_pct: float = 0.0   # passive benchmark for this bar window
    daily_returns: list[float] = field(default_factory=list)


def run_backtest(bars: list[DailyBar], params: BacktestParams) -> BacktestResult:
    cash = params.starting_cash
    btc_held = 0.0
    trades: list[tuple[str, float, float]] = []  # (action, price, quantity)
    portfolio_values: list[float] = []
    bayesian = BayesianUpdater()

    for i, bar in enumerate(bars):
        current_value = cash + btc_held * bar.price
        portfolio_values.append(current_value)

        if i < params.lookback_days:
            continue

        window = bars[i - params.lookback_days: i]
        fgi_readings = [
            FGIReading(value=b.fgi, label="", timestamp=b.date_ts, source="backtest")
            for b in window
        ]
        current_fgi = FGIReading(value=bar.fgi, label="", timestamp=bar.date_ts, source="backtest")

        signal = compute_signal(
            fgi_readings,
            current_fgi,
            bayesian,
            buy_z_threshold=params.buy_z_threshold,
            sell_z_threshold=params.sell_z_threshold,
        )

        if signal.confidence < params.min_confidence:
            continue

        # Apply spread cost (round-trip is 2× spread, applied at execution)
        effective_buy_price = bar.price * (1 + params.spread_pct)
        effective_sell_price = bar.price * (1 - params.spread_pct)

        if params.scale_sell_with_z:
            z_pos = signal.z_score
            thresh = params.sell_z_threshold
            scale = min(1.0, max(0.0,
                (z_pos - thresh) / max(params.max_sell_z - thresh, 1e-6)
            ))
            _sell_pct = params.min_sell_pct + scale * (params.max_sell_pct - params.min_sell_pct)
        else:
            _sell_pct = params.sell_pct if params.sell_pct is not None else params.position_size_pct

        if signal.action == "BUY" and cash > 1.0:
            if params.scale_buy_with_z:
                z_abs = abs(signal.z_score)
                thresh_abs = abs(params.buy_z_threshold)
                scale = min(1.0, max(0.0,
                    (z_abs - thresh_abs) / max(params.max_scale_z - thresh_abs, 1e-6)
                ))
                _buy_pct = params.min_buy_pct + scale * (params.max_buy_pct - params.min_buy_pct)
            else:
                _buy_pct = params.position_size_pct
            usd_to_spend = cash * _buy_pct
            qty = usd_to_spend / effective_buy_price
            cash -= usd_to_spend
            btc_held += qty
            trades.append(("BUY", effective_buy_price, qty))

        elif signal.action == "SELL" and btc_held > 0:
            qty = btc_held * _sell_pct
            usd_received = qty * effective_sell_price
            btc_held -= qty
            cash += usd_received
            trades.append(("SELL", effective_sell_price, qty))

            # Update Bayesian: was the last BUY profitable?
            buy_trades = [t for t in trades if t[0] == "BUY"]
            if buy_trades:
                last_buy_price = buy_trades[-1][1]
                bayesian.update("BUY", bar.price > last_buy_price)

    final_value = cash + btc_held * bars[-1].price if bars else params.starting_cash
    total_return = (final_value - params.starting_cash) / params.starting_cash * 100

    # Sharpe ratio (annualised, daily returns, risk-free = 0)
    daily_rets: list[float] = []
    for j in range(1, len(portfolio_values)):
        prev = portfolio_values[j - 1]
        if prev > 0:
            daily_rets.append((portfolio_values[j] - prev) / prev)

    sharpe = 0.0
    if len(daily_rets) > 1:
        mean_r = np.mean(daily_rets)
        std_r = np.std(daily_rets, ddof=1)
        if std_r > 0:
            sharpe = float(mean_r / std_r * np.sqrt(365))

    # Max drawdown
    peak = params.starting_cash
    max_dd = 0.0
    for v in portfolio_values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Win rate: count BUY→SELL pairs
    wins = 0
    buy_stack: list[float] = []
    for action, price, _ in trades:
        if action == "BUY":
            buy_stack.append(price)
        elif action == "SELL" and buy_stack:
            entry = buy_stack.pop()
            if price > entry:
                wins += 1

    num_sells = sum(1 for t in trades if t[0] == "SELL")
    win_rate = wins / num_sells if num_sells > 0 else 0.0

    # Buy-and-hold benchmark: invest all cash at first bar, sell at last
    buy_hold_return = 0.0
    if bars:
        bh_qty = params.starting_cash / (bars[0].price * (1 + params.spread_pct))
        bh_value = bh_qty * bars[-1].price * (1 - params.spread_pct)
        buy_hold_return = (bh_value - params.starting_cash) / params.starting_cash * 100

    return BacktestResult(
        params=params,
        total_return_pct=round(total_return, 2),
        sharpe_ratio=round(sharpe, 3),
        max_drawdown_pct=round(max_dd, 2),
        num_trades=len(trades),
        win_rate=round(win_rate, 3),
        final_portfolio_usd=round(final_value, 2),
        buy_hold_return_pct=round(buy_hold_return, 2),
        daily_returns=daily_rets,
    )


# ── Monte Carlo ───────────────────────────────────────────────────────────────

def run_monte_carlo(
    bars: list[DailyBar],
    n_simulations: int = 200,
    seed: int = 42,
    symbol: str = "BTC-USD",
    starting_cash: float = 10_000.0,
) -> list[BacktestResult]:
    """
    Run n_simulations backtests with randomised parameters.
    Returns results sorted by Sharpe ratio descending.
    """
    rng = random.Random(seed)
    spread = SPREAD_PCT.get(symbol, 0.01)
    results: list[BacktestResult] = []

    logger.info("Running %d Monte Carlo simulations for %s…", n_simulations, symbol)
    for i in range(n_simulations):
        params = BacktestParams(
            buy_z_threshold=rng.uniform(-3.0, -0.5),
            sell_z_threshold=rng.uniform(0.5, 3.0),
            position_size_pct=rng.uniform(0.05, 0.50),
            lookback_days=rng.randint(30, 180),
            min_confidence=rng.uniform(0.50, 0.80),
            starting_cash=starting_cash,
            symbol=symbol,
            spread_pct=spread,
        )
        try:
            result = run_backtest(bars, params)
            results.append(result)
        except Exception as exc:
            logger.debug("Simulation %d failed: %s", i, exc)

        if (i + 1) % 50 == 0:
            logger.info("  %d/%d simulations complete", i + 1, n_simulations)

    results.sort(key=lambda r: r.sharpe_ratio, reverse=True)
    return results


def run_asymmetric_monte_carlo(
    bars: list[DailyBar],
    n_simulations: int = 200,
    seed: int = 99,
    symbol: str = "BTC-USD",
    starting_cash: float = 10_000.0,
) -> list[BacktestResult]:
    """
    Same as run_monte_carlo but buy_pct and sell_pct vary independently.
    Identical signal logic — isolates the effect of asymmetric sizing.
    """
    rng = random.Random(seed)
    spread = SPREAD_PCT.get(symbol, 0.01)
    results: list[BacktestResult] = []

    logger.info("Running %d asymmetric Monte Carlo simulations for %s…", n_simulations, symbol)
    for i in range(n_simulations):
        params = BacktestParams(
            buy_z_threshold=rng.uniform(-3.0, -0.5),
            sell_z_threshold=rng.uniform(0.5, 3.0),
            position_size_pct=rng.uniform(0.05, 0.75),   # buy fraction
            sell_pct=rng.uniform(0.05, 0.75),             # sell fraction — independent
            lookback_days=rng.randint(30, 180),
            min_confidence=rng.uniform(0.50, 0.80),
            starting_cash=starting_cash,
            symbol=symbol,
            spread_pct=spread,
        )
        try:
            result = run_backtest(bars, params)
            results.append(result)
        except Exception as exc:
            logger.debug("Asymmetric simulation %d failed: %s", i, exc)

        if (i + 1) % 50 == 0:
            logger.info("  %d/%d asymmetric simulations complete", i + 1, n_simulations)

    results.sort(key=lambda r: r.sharpe_ratio, reverse=True)
    return results


def run_scaled_monte_carlo(
    bars: list[DailyBar],
    n_simulations: int = 200,
    seed: int = 77,
    symbol: str = "BTC-USD",
    starting_cash: float = 10_000.0,
) -> list[BacktestResult]:
    """
    Monte Carlo with z-score-scaled buy sizing.
    Buy fraction ramps from min_buy_pct (at threshold) to max_buy_pct (at max_scale_z).
    sell_pct varies independently. Everything else matches run_asymmetric_monte_carlo.
    """
    rng = random.Random(seed)
    spread = SPREAD_PCT.get(symbol, 0.01)
    results: list[BacktestResult] = []

    logger.info("Running %d scaled Monte Carlo simulations for %s…", n_simulations, symbol)
    for i in range(n_simulations):
        min_buy = rng.uniform(0.05, 0.40)
        max_buy = rng.uniform(min_buy + 0.10, 0.95)   # always > min_buy, up to 95%
        params = BacktestParams(
            buy_z_threshold=rng.uniform(-3.0, -0.5),
            sell_z_threshold=rng.uniform(0.5, 3.0),
            lookback_days=rng.randint(30, 180),
            min_confidence=rng.uniform(0.50, 0.80),
            sell_pct=rng.uniform(0.05, 0.75),
            starting_cash=starting_cash,
            symbol=symbol,
            spread_pct=spread,
            scale_buy_with_z=True,
            min_buy_pct=min_buy,
            max_buy_pct=max_buy,
            max_scale_z=rng.uniform(2.5, 5.0),
        )
        try:
            result = run_backtest(bars, params)
            results.append(result)
        except Exception as exc:
            logger.debug("Scaled simulation %d failed: %s", i, exc)

        if (i + 1) % 50 == 0:
            logger.info("  %d/%d scaled simulations complete", i + 1, n_simulations)

    results.sort(key=lambda r: r.sharpe_ratio, reverse=True)
    return results


def run_scaled_sell_monte_carlo(
    bars: list[DailyBar],
    n_simulations: int = 200,
    seed: int = 55,
    symbol: str = "SOL-USD",
    starting_cash: float = 10_000.0,
) -> list[BacktestResult]:
    """
    Z-scaled buy AND z-scaled sell. Sell fraction ramps from min_sell_pct
    (at sell threshold) to max_sell_pct (at max_sell_z).
    """
    rng = random.Random(seed)
    spread = SPREAD_PCT.get(symbol, 0.01)
    results: list[BacktestResult] = []

    logger.info("Running %d scaled-sell Monte Carlo simulations for %s…", n_simulations, symbol)
    for i in range(n_simulations):
        min_buy = rng.uniform(0.05, 0.40)
        max_buy = rng.uniform(min_buy + 0.10, 0.95)
        min_sell = rng.uniform(0.10, 0.50)
        max_sell = rng.uniform(min_sell + 0.10, 0.99)
        params = BacktestParams(
            buy_z_threshold=rng.uniform(-3.0, -0.5),
            sell_z_threshold=rng.uniform(0.5, 3.0),
            lookback_days=rng.randint(30, 180),
            min_confidence=rng.uniform(0.50, 0.80),
            starting_cash=starting_cash,
            symbol=symbol,
            spread_pct=spread,
            scale_buy_with_z=True,
            min_buy_pct=min_buy,
            max_buy_pct=max_buy,
            max_scale_z=rng.uniform(2.5, 5.0),
            scale_sell_with_z=True,
            min_sell_pct=min_sell,
            max_sell_pct=max_sell,
            max_sell_z=rng.uniform(2.5, 5.0),
        )
        try:
            result = run_backtest(bars, params)
            results.append(result)
        except Exception as exc:
            logger.debug("Scaled-sell simulation %d failed: %s", i, exc)

        if (i + 1) % 50 == 0:
            logger.info("  %d/%d scaled-sell simulations complete", i + 1, n_simulations)

    results.sort(key=lambda r: r.sharpe_ratio, reverse=True)
    return results


def print_monte_carlo_summary(results: list[BacktestResult], symbol: str = "") -> None:
    if not results:
        print("No simulation results.")
        return

    label = f" — {symbol}" if symbol else ""
    returns = [r.total_return_pct for r in results]
    sharpes = [r.sharpe_ratio for r in results]
    drawdowns = [r.max_drawdown_pct for r in results]

    print("\n" + "=" * 60)
    print(f"Monte Carlo Summary{label}  ({len(results)} simulations)")
    print("=" * 60)
    print(f"{'Metric':<28} {'p10':>8} {'median':>8} {'p90':>8}")
    print("-" * 60)
    for label, series in [("Total return (%)", returns), ("Sharpe ratio", sharpes), ("Max drawdown (%)", drawdowns)]:
        p10, med, p90 = np.percentile(series, [10, 50, 90])
        print(f"{label:<28} {p10:>8.2f} {med:>8.2f} {p90:>8.2f}")
    print("=" * 60)

    best = results[0]
    print(f"\nBest simulation (Sharpe {best.sharpe_ratio:.3f}):")
    print(f"  buy_z_threshold   : {best.params.buy_z_threshold:.2f}")
    print(f"  sell_z_threshold  : {best.params.sell_z_threshold:.2f}")
    print(f"  position_size_pct : {best.params.position_size_pct:.2%}")
    print(f"  lookback_days     : {best.params.lookback_days}")
    print(f"  min_confidence    : {best.params.min_confidence:.2f}")
    print(f"  total_return      : {best.total_return_pct:.2f}%")
    print(f"  max_drawdown      : {best.max_drawdown_pct:.2f}%")
    print(f"  win_rate          : {best.win_rate:.1%}")
    print(f"  num_trades        : {best.num_trades}")
    print()
