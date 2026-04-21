"""
Signal generation from Fear & Greed Index history.

Pipeline:
  1. Z-score normalization over configurable lookback window
  2. Bayesian confidence update (Beta-Binomial) from live trade outcomes
  3. Final signal: BUY | SELL | HOLD with confidence score [0, 1]
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from fgi import FGIReading

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    action: str        # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0–1.0
    z_score: float
    fgi_value: int
    fgi_mean: float
    fgi_std: float
    reason: str


class BayesianUpdater:
    """
    Beta-Binomial model tracking per-action success rates.

    Prior is initialised at 55% success (weakly informative — slightly better
    than a coin flip, consistent with contrarian FGI strategies in literature).
    Each confirmed profitable trade increments alpha; each loss increments beta.
    """

    def __init__(self, prior_successes: float = 5.5, prior_trials: float = 10.0) -> None:
        # Buy arm
        self._buy_alpha: float = prior_successes
        self._buy_beta: float = prior_trials - prior_successes
        # Sell arm
        self._sell_alpha: float = prior_successes
        self._sell_beta: float = prior_trials - prior_successes

    def update(self, action: str, success: bool) -> None:
        """Call this after a trade closes to update the posterior."""
        if action == "BUY":
            if success:
                self._buy_alpha += 1
            else:
                self._buy_beta += 1
        elif action == "SELL":
            if success:
                self._sell_alpha += 1
            else:
                self._sell_beta += 1

    def confidence(self, action: str) -> float:
        """Posterior mean of the Beta distribution for this action."""
        if action == "BUY":
            return self._buy_alpha / (self._buy_alpha + self._buy_beta)
        if action == "SELL":
            return self._sell_alpha / (self._sell_alpha + self._sell_beta)
        return 0.5

    def effective_sample_size(self, action: str) -> float:
        """How many pseudo-observations are backing the current estimate."""
        if action == "BUY":
            return self._buy_alpha + self._buy_beta
        return self._sell_alpha + self._sell_beta

    def state(self) -> dict[str, float]:
        return {
            "buy_alpha": self._buy_alpha,
            "buy_beta": self._buy_beta,
            "sell_alpha": self._sell_alpha,
            "sell_beta": self._sell_beta,
        }

    @classmethod
    def from_state(cls, state: dict[str, float]) -> "BayesianUpdater":
        obj = cls.__new__(cls)
        obj._buy_alpha = state["buy_alpha"]
        obj._buy_beta = state["buy_beta"]
        obj._sell_alpha = state["sell_alpha"]
        obj._sell_beta = state["sell_beta"]
        return obj


def _zscore_to_confidence(z: float) -> float:
    """
    Map |z| → [0, 1] using the standard normal CDF tail probability,
    so a z of ±1.5 gives ~0.93 and ±2.0 gives ~0.98.
    This is more principled than a linear cap.
    """
    # Two-tailed p-value converted to confidence: conf = 1 - 2*Φ(-|z|)
    # Approximation of Φ(x) via Abramowitz & Stegun (error < 7.5e-8)
    def phi(x: float) -> float:
        t = 1.0 / (1.0 + 0.2316419 * abs(x))
        poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
        return (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly

    p_tail = phi(abs(z))
    return min(1.0, max(0.0, 1.0 - 2 * p_tail))


def compute_signal(
    history: list[FGIReading],
    current: FGIReading,
    bayesian: BayesianUpdater,
    buy_z_threshold: float = -1.5,
    sell_z_threshold: float = 1.5,
) -> Signal:
    """
    Compute a trading signal from the current FGI reading relative to history.

    confidence = blend of:
      - z-score tail probability (how extreme the current reading is)
      - Bayesian posterior success rate for this action
    weighted 60/40 toward the statistical signal vs. learned performance.
    """
    if len(history) < 14:
        return Signal("HOLD", 0.0, 0.0, current.value, 0.0, 0.0,
                      f"Insufficient history ({len(history)} days, need ≥14)")

    values = np.array([r.value for r in history], dtype=float)
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=1))

    if std < 1e-6:
        return Signal("HOLD", 0.0, 0.0, current.value, mean, std,
                      "FGI history has no variance")

    z = (current.value - mean) / std

    if z <= buy_z_threshold:
        stat_conf = _zscore_to_confidence(z)
        bayes_conf = bayesian.confidence("BUY")
        confidence = round(0.6 * stat_conf + 0.4 * bayes_conf, 4)
        reason = (
            f"FGI {current.value} is {abs(z):.2f}σ below {len(history)}-day mean "
            f"({mean:.1f}±{std:.1f}) — extreme fear, contrarian buy"
        )
        logger.info("BUY signal  z=%.2f  conf=%.2f  reason=%s", z, confidence, reason)
        return Signal("BUY", confidence, z, current.value, mean, std, reason)

    if z >= sell_z_threshold:
        stat_conf = _zscore_to_confidence(z)
        bayes_conf = bayesian.confidence("SELL")
        confidence = round(0.6 * stat_conf + 0.4 * bayes_conf, 4)
        reason = (
            f"FGI {current.value} is {z:.2f}σ above {len(history)}-day mean "
            f"({mean:.1f}±{std:.1f}) — extreme greed, contrarian sell"
        )
        logger.info("SELL signal z=%.2f  conf=%.2f  reason=%s", z, confidence, reason)
        return Signal("SELL", confidence, z, current.value, mean, std, reason)

    reason = (
        f"FGI {current.value} z={z:.2f} within normal range "
        f"[{buy_z_threshold:.1f}, {sell_z_threshold:.1f}]"
    )
    logger.debug("HOLD  z=%.2f", z)
    return Signal("HOLD", 0.5, z, current.value, mean, std, reason)
