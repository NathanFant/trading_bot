"""
Fear & Greed Index fetcher with multi-source fallback.

Source priority:
  1. Alternative.me  (free, no key)
  2. CoinMarketCap   (free tier, key required)
  3. Calculated      (derived from BTC price volatility via CoinGecko)
  4. Last cached     (database fallback — never returns stale data without warning)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

BASE_TIMEOUT = 10


@dataclass
class FGIReading:
    value: int           # 0–100
    label: str           # Extreme Fear | Fear | Neutral | Greed | Extreme Greed
    timestamp: int       # unix seconds
    source: str


def _label(value: int) -> str:
    if value <= 24:
        return "Extreme Fear"
    if value <= 44:
        return "Fear"
    if value <= 55:
        return "Neutral"
    if value <= 74:
        return "Greed"
    return "Extreme Greed"


# ── Source 1: Alternative.me ──────────────────────────────────────────────────

def _from_alternative_me(limit: int = 1) -> list[FGIReading]:
    resp = requests.get(
        f"https://api.alternative.me/fng/?limit={limit}",
        timeout=BASE_TIMEOUT,
    )
    resp.raise_for_status()
    return [
        FGIReading(
            value=int(d["value"]),
            label=d["value_classification"],
            timestamp=int(d["timestamp"]),
            source="alternative.me",
        )
        for d in resp.json()["data"]
    ]


# ── Source 2: CoinMarketCap ───────────────────────────────────────────────────

def _from_coinmarketcap(api_key: str) -> list[FGIReading]:
    resp = requests.get(
        "https://pro-api.coinmarketcap.com/v3/fear-and-greed/latest",
        headers={"X-CMC_PRO_API_KEY": api_key},
        timeout=BASE_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    value = int(float(data["value"]))
    return [FGIReading(value=value, label=_label(value), timestamp=int(time.time()), source="coinmarketcap")]


# ── Source 3: Derived from BTC volatility (CoinGecko, free) ──────────────────
# Simple heuristic: 30-day rolling volatility mapped to a 0-100 fear/greed score.
# High volatility + price drop  → fear; low volatility + price rise → greed.

def _from_coingecko_derived() -> list[FGIReading]:
    resp = requests.get(
        "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
        "?vs_currency=usd&days=30&interval=daily",
        timeout=BASE_TIMEOUT,
    )
    resp.raise_for_status()
    prices = [p[1] for p in resp.json()["prices"]]
    if len(prices) < 2:
        raise ValueError("Insufficient price data from CoinGecko")

    returns = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]
    volatility = (sum(r**2 for r in returns) / len(returns)) ** 0.5  # RMS
    momentum = (prices[-1] - prices[0]) / prices[0]

    # Map to 0–100: high vol + negative momentum → low (fear); opposite → high (greed)
    vol_score = max(0.0, 1.0 - volatility * 20)        # dampens at ~5% daily vol
    mom_score = min(1.0, max(0.0, 0.5 + momentum * 2)) # 0 at -25%, 1 at +25%
    raw = int((vol_score * 0.4 + mom_score * 0.6) * 100)
    value = max(0, min(100, raw))

    return [FGIReading(value=value, label=_label(value), timestamp=int(time.time()), source="coingecko-derived")]


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_current(
    coinmarketcap_api_key: str = "",
    cached_fallback: FGIReading | None = None,
) -> FGIReading:
    """Return the latest FGI reading, trying sources in priority order."""
    sources: list[tuple[str, object]] = [
        ("alternative.me", lambda: _from_alternative_me(1)[0]),
    ]
    if coinmarketcap_api_key:
        sources.append(("coinmarketcap", lambda: _from_coinmarketcap(coinmarketcap_api_key)[0]))
    sources.append(("coingecko-derived", lambda: _from_coingecko_derived()[0]))

    for name, fn in sources:
        try:
            reading = fn()  # type: ignore[operator]
            logger.info("FGI from %s: %d (%s)", name, reading.value, reading.label)
            return reading
        except Exception as exc:
            logger.warning("FGI source %s failed: %s", name, exc)

    if cached_fallback is not None:
        age_hours = (time.time() - cached_fallback.timestamp) / 3600
        logger.warning("All live FGI sources failed — using cached value (%.1fh old)", age_hours)
        return cached_fallback

    raise RuntimeError("All FGI sources failed and no cached fallback available")


def fetch_history(days: int = 180, coinmarketcap_api_key: str = "") -> list[FGIReading]:
    """Return up to `days` daily FGI readings, oldest-first."""
    sources: list[tuple[str, object]] = [
        ("alternative.me", lambda: _from_alternative_me(days)),
    ]
    if coinmarketcap_api_key:
        # CMC historical endpoint
        def _cmc_history() -> list[FGIReading]:
            resp = requests.get(
                f"https://pro-api.coinmarketcap.com/v3/fear-and-greed/historical?limit={days}",
                headers={"X-CMC_PRO_API_KEY": coinmarketcap_api_key},
                timeout=15,
            )
            resp.raise_for_status()
            out = []
            for d in resp.json()["data"]:
                v = int(float(d["value"]))
                out.append(FGIReading(value=v, label=_label(v), timestamp=int(d["timestamp"]), source="coinmarketcap"))
            return out
        sources.append(("coinmarketcap", _cmc_history))

    for name, fn in sources:
        try:
            readings = fn()  # type: ignore[operator]
            readings.sort(key=lambda r: r.timestamp)
            logger.info("Loaded %d historical FGI readings from %s", len(readings), name)
            return readings
        except Exception as exc:
            logger.warning("History source %s failed: %s", name, exc)

    raise RuntimeError("Could not fetch FGI history from any source")
