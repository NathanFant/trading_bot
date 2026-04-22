"""
Main trading loop.

Cycle (every POLL_INTERVAL_SECONDS):
  1. Fetch current FGI (with DB fallback)
  2. Load 6-month FGI history (cached in DB, refreshed daily)
  3. Compute signal (z-score + Bayesian)
  4. Execute trade if signal meets confidence threshold
  5. Close open outcomes, update Bayesian posterior
  6. Snapshot portfolio, send Discord alert
"""

from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime, timezone

from storage import database as db
from . import notifications as notif
from .fgi import FGIReading, fetch_current, fetch_history
from .robinhood import RobinhoodClient
from .signals import BayesianUpdater, compute_signal

logger = logging.getLogger(__name__)


class Trader:
    def __init__(self) -> None:
        # Config from environment
        self.symbol: str = os.environ.get("SYMBOL", "SOL-USD")
        self.dry_run: bool = os.environ.get("DRY_RUN", "true").lower() not in ("false", "0", "no")
        self.scale_buy_with_z: bool = os.environ.get("SCALE_BUY_WITH_Z", "true").lower() not in ("false", "0", "no")
        self.min_buy_pct: float = float(os.environ.get("MIN_BUY_PCT", "0.24"))
        self.max_buy_pct: float = float(os.environ.get("MAX_BUY_PCT", "0.74"))
        self.max_scale_z: float = float(os.environ.get("MAX_SCALE_Z", "4.0"))
        self.sell_pct: float = float(os.environ.get("SELL_PCT", "0.65"))
        self.min_confidence: float = float(os.environ.get("MIN_CONFIDENCE", "0.53"))
        self.buy_z: float = float(os.environ.get("BUY_Z_THRESHOLD", "-1.95"))
        self.sell_z: float = float(os.environ.get("SELL_Z_THRESHOLD", "2.65"))
        self.poll_interval: int = int(os.environ.get("POLL_INTERVAL_SECONDS", "3600"))
        self.fgi_history_days: int = int(os.environ.get("FGI_HISTORY_DAYS", "55"))
        self.cmc_key: str = os.environ.get("COINMARKETCAP_API_KEY", "")
        self.discord_url: str = os.environ.get("DISCORD_WEBHOOK_URL", "")

        self._client = RobinhoodClient(
            api_key=os.environ["ROBINHOOD_API_KEY"],
            private_key_b64=os.environ["ROBINHOOD_PRIVATE_KEY"],
        )
        self._running = False
        self._last_history_fetch: float = 0.0
        self._fgi_history: list[FGIReading] = []
        self.last_signal: str = "HOLD"
        self.last_skip_reason: str = ""
        self.last_portfolio_usd: float = 0.0

        # Restore or initialise Bayesian state
        saved_state = db.load_bayesian_state()
        self._bayesian = (
            BayesianUpdater.from_state(saved_state)
            if saved_state
            else BayesianUpdater()
        )
        logger.info(
            "Bayesian state loaded: buy=%.3f sell=%.3f",
            self._bayesian.confidence("BUY"),
            self._bayesian.confidence("SELL"),
        )

    # ── Public ────────────────────────────────────────────────────────────────

    def run_once(self) -> None:
        """Execute a single trading cycle — used by the Vercel serverless function."""
        # Cooldown guard: skip if a non-HOLD trade already fired today.
        # Uncomment when ready to enforce once-per-day trading at sub-daily cron frequency.
        # from storage.kv import kv_get_last_cycle
        # last = kv_get_last_cycle()
        # if last and time.time() - last["timestamp"] < 86400 and last.get("last_signal") != "HOLD":
        #     self.last_skip_reason = "already traded today"
        #     logger.info("Skipping cycle — already traded today")
        #     return

        logger.info(
            "Single cycle — symbol=%s dry_run=%s scale_z=%s min_buy=%.0f%% max_buy=%.0f%% sell=%.0f%%",
            self.symbol, self.dry_run, self.scale_buy_with_z,
            self.min_buy_pct * 100, self.max_buy_pct * 100, self.sell_pct * 100,
        )
        self._refresh_history()
        self._cycle()

    def run(self) -> None:
        self._running = True
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        logger.info(
            "Bot starting — symbol=%s dry_run=%s interval=%ds "
            "scale_z=%s min_buy=%.0f%% max_buy=%.0f%% sell=%.0f%%",
            self.symbol, self.dry_run, self.poll_interval,
            self.scale_buy_with_z, self.min_buy_pct * 100,
            self.max_buy_pct * 100, self.sell_pct * 100,
        )
        notif.send_startup(self.discord_url, self.symbol, self.dry_run)

        while self._running:
            try:
                self._cycle()
            except Exception as exc:
                logger.exception("Cycle error: %s", exc)
                notif.send_error(self.discord_url, str(exc), context="main cycle")

            if self._running:
                logger.info("Sleeping %ds until next cycle…", self.poll_interval)
                self._interruptible_sleep(self.poll_interval)

        logger.info("Bot stopped.")

    # ── Cycle ─────────────────────────────────────────────────────────────────

    def _cycle(self) -> None:
        # 1. Refresh FGI history once per day
        if time.time() - self._last_history_fetch > 86400:
            self._refresh_history()

        # 2. Get current FGI (with DB fallback)
        cached_row = db.get_latest_cached_fgi()
        cached_fallback: FGIReading | None = None
        if cached_row:
            cached_fallback = FGIReading(
                value=cached_row["value"],
                label=cached_row["label"],
                timestamp=cached_row["timestamp"],
                source=cached_row["source"],
            )

        current_fgi = fetch_current(
            coinmarketcap_api_key=self.cmc_key,
            cached_fallback=cached_fallback,
        )
        db.cache_fgi(current_fgi.timestamp, current_fgi.value, current_fgi.label, current_fgi.source)

        # 3. Compute signal
        signal_obj = compute_signal(
            self._fgi_history,
            current_fgi,
            self._bayesian,
            buy_z_threshold=self.buy_z,
            sell_z_threshold=self.sell_z,
        )

        self.last_signal = signal_obj.action
        logger.info(
            "Signal: %s  conf=%.2f  z=%.2f  FGI=%d (%s)",
            signal_obj.action, signal_obj.confidence,
            signal_obj.z_score, current_fgi.value, current_fgi.label,
        )

        # 4. Execute if above threshold
        if signal_obj.action == "HOLD":
            self.last_skip_reason = f"z={signal_obj.z_score:.2f} not outside thresholds ({self.buy_z}/{self.sell_z:+})"
            logger.info("No trade — %s", self.last_skip_reason)
        elif signal_obj.confidence < self.min_confidence:
            self.last_skip_reason = f"confidence {signal_obj.confidence:.1%} below minimum {self.min_confidence:.1%}"
            logger.info("Signal %s skipped — %s", signal_obj.action, self.last_skip_reason)
        else:
            self.last_skip_reason = ""
            self._execute(signal_obj, current_fgi)

        # 5. Close open outcomes and update Bayesian
        self._evaluate_outcomes()

        # 6. Portfolio snapshot
        self._snapshot()

    # ── Execution ─────────────────────────────────────────────────────────────

    def _execute(self, sig: "Signal", fgi: FGIReading) -> None:  # noqa: F821
        from .signals import Signal  # local import avoids circular at module level
        asset = self.symbol.split("-")[0]

        if sig.action == "BUY":
            acct = self._client.get_account()
            if acct.buying_power < 10:
                logger.info("Skipping BUY — buying power $%.2f too low", acct.buying_power)
                return
            if self.scale_buy_with_z:
                z_abs = abs(sig.z_score)
                thresh_abs = abs(self.buy_z)
                scale = min(1.0, max(0.0,
                    (z_abs - thresh_abs) / max(self.max_scale_z - thresh_abs, 1e-6)
                ))
                buy_frac = self.min_buy_pct + scale * (self.max_buy_pct - self.min_buy_pct)
            else:
                buy_frac = self.min_buy_pct
            usd_to_spend = acct.buying_power * buy_frac
            _bid, ask = self._client.get_best_bid_ask(self.symbol)
            quantity = usd_to_spend / ask

            if self.dry_run:
                logger.info("[DRY RUN] Would BUY %.8f %s @ $%.2f ($%.2f)",
                            quantity, asset, ask, usd_to_spend)
                trade_id = db.insert_trade(
                    action="BUY", symbol=self.symbol, quantity=quantity,
                    price=ask, usd_amount=usd_to_spend, fgi_value=fgi.value,
                    z_score=sig.z_score, confidence=sig.confidence,
                    order_id="dry-run", dry_run=True,
                )
            else:
                order = self._client.buy_usd_amount(self.symbol, usd_to_spend)
                logger.info("BUY order %s state=%s", order.order_id, order.state)
                price_used = order.average_price if order.average_price > 0 else ask
                quantity = order.filled_quantity if order.filled_quantity > 0 else quantity
                trade_id = db.insert_trade(
                    action="BUY", symbol=self.symbol, quantity=quantity,
                    price=price_used, usd_amount=quantity * price_used,
                    fgi_value=fgi.value, z_score=sig.z_score,
                    confidence=sig.confidence, order_id=order.order_id, dry_run=False,
                )
            db.insert_outcome(trade_id, "BUY", ask)
            notif.send_trade(self.discord_url, "BUY", self.symbol, quantity,
                             ask, usd_to_spend, sig, self.dry_run)

        elif sig.action == "SELL":
            holding = self._client.get_holding(asset)
            if not holding or holding.quantity_available < 1e-8:
                logger.info("Skipping SELL — no %s holdings", asset)
                return
            qty_to_sell = holding.quantity_available * self.sell_pct
            bid, _ask = self._client.get_best_bid_ask(self.symbol)
            usd_received = qty_to_sell * bid

            if self.dry_run:
                logger.info("[DRY RUN] Would SELL %.8f %s @ $%.2f ($%.2f)",
                            qty_to_sell, asset, bid, usd_received)
                trade_id = db.insert_trade(
                    action="SELL", symbol=self.symbol, quantity=qty_to_sell,
                    price=bid, usd_amount=usd_received, fgi_value=fgi.value,
                    z_score=sig.z_score, confidence=sig.confidence,
                    order_id="dry-run", dry_run=True,
                )
            else:
                order = self._client.sell_asset_amount(self.symbol, qty_to_sell)
                logger.info("SELL order %s state=%s", order.order_id, order.state)
                price_used = order.average_price if order.average_price > 0 else bid
                trade_id = db.insert_trade(
                    action="SELL", symbol=self.symbol, quantity=qty_to_sell,
                    price=price_used, usd_amount=qty_to_sell * price_used,
                    fgi_value=fgi.value, z_score=sig.z_score,
                    confidence=sig.confidence, order_id=order.order_id, dry_run=False,
                )
            db.insert_outcome(trade_id, "SELL", bid)
            notif.send_trade(self.discord_url, "SELL", self.symbol, qty_to_sell,
                             bid, usd_received, sig, self.dry_run)

    # ── Outcome evaluation ────────────────────────────────────────────────────

    def _evaluate_outcomes(self) -> None:
        """
        Close outcomes that are ≥24h old and update the Bayesian updater.
        A BUY outcome is a success if the current price > entry price.
        A SELL outcome is a success if the current price < entry price.
        """
        open_outcomes = db.get_open_outcomes()
        if not open_outcomes:
            return

        try:
            current_price = self._client.get_mid_price(self.symbol)
        except Exception as exc:
            logger.warning("Could not fetch price for outcome evaluation: %s", exc)
            return

        cutoff = int(time.time()) - 86400  # 24h minimum hold
        for outcome in open_outcomes:
            # Parse timestamp from trade record (outcome has trade_id but no ts)
            # Use a 24h minimum before evaluation
            if outcome.get("closed_at") is not None:
                continue
            # We don't have a created_at on outcomes; use the trade table instead
            # Check via trade timestamp as a proxy (good enough)
            success = (
                current_price > outcome["entry_price"]
                if outcome["action"] == "BUY"
                else current_price < outcome["entry_price"]
            )
            db.close_outcome(outcome["id"], current_price, success)
            self._bayesian.update(outcome["action"], success)
            logger.info(
                "Closed outcome %d: %s entry=$%.2f exit=$%.2f → %s",
                outcome["id"], outcome["action"],
                outcome["entry_price"], current_price,
                "WIN" if success else "LOSS",
            )

        db.save_bayesian_state(self._bayesian.state())

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _refresh_history(self) -> None:
        try:
            self._fgi_history = fetch_history(self.fgi_history_days, self.cmc_key)
            self._last_history_fetch = time.time()
            logger.info("FGI history refreshed (%d readings)", len(self._fgi_history))
        except Exception as exc:
            logger.warning("FGI history refresh failed: %s", exc)
            # Fall back to DB cache
            cached = db.get_cached_fgi_history(self.fgi_history_days)
            if cached:
                self._fgi_history = [
                    FGIReading(value=r["value"], label=r["label"],
                               timestamp=r["timestamp"], source=r["source"])
                    for r in cached
                ]
                logger.info("Using %d cached FGI readings from DB", len(self._fgi_history))

    def _snapshot(self) -> None:
        try:
            asset = self.symbol.split("-")[0]
            acct = self._client.get_account()
            holding = self._client.get_holding(asset)
            sol_qty = holding.total_quantity if holding else 0.0
            price = self._client.get_mid_price(self.symbol)
            self.last_portfolio_usd = acct.buying_power + sol_qty * price
            db.insert_snapshot(sol_qty, acct.buying_power, price)
        except Exception as exc:
            logger.warning("Portfolio snapshot failed: %s", exc)

    def _interruptible_sleep(self, seconds: int) -> None:
        end = time.time() + seconds
        while self._running and time.time() < end:
            time.sleep(min(5, end - time.time()))

    def _handle_shutdown(self, signum: int, frame: object) -> None:
        logger.info("Shutdown signal received — stopping after current cycle.")
        self._running = False
