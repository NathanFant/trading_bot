"""
Robinhood Crypto API client (v1 endpoints).

Auth: Ed25519-signed headers (x-api-key, x-timestamp, x-signature).
Signing message = api_key + timestamp + path_with_querystring + METHOD + body
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import nacl.signing  # type: ignore[import-untyped]
import requests
from requests import Response

logger = logging.getLogger(__name__)

BASE_URL = "https://trading.robinhood.com"
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0


@dataclass
class AccountInfo:
    account_number: str
    buying_power: float
    currency: str


@dataclass
class Holding:
    asset_code: str
    total_quantity: float
    quantity_available: float


@dataclass
class OrderResult:
    order_id: str
    client_order_id: str
    state: str              # "open" | "filled" | "canceled" | "failed" | "pending"
    side: str
    symbol: str
    filled_quantity: float
    average_price: float


class RobinhoodClient:
    def __init__(self, api_key: str, private_key_b64: str) -> None:
        self._api_key = api_key
        self._private_key = nacl.signing.SigningKey(base64.b64decode(private_key_b64))
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        })

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _sign_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        """
        path must include the query string if present.
        Message = api_key + timestamp + path + METHOD + body  (per Robinhood docs)
        """
        timestamp = str(int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp()))
        message = self._api_key + timestamp + path + method.upper() + body
        sig_bytes: bytes = self._private_key.sign(message.encode("utf-8")).signature
        return {
            "x-api-key": self._api_key,
            "x-timestamp": timestamp,
            "x-signature": base64.b64encode(sig_bytes).decode("utf-8"),
        }

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body_str = json.dumps(payload) if payload else ""
        signed_path = path + ("?" + urlencode(params) if params else "")
        url = BASE_URL + path

        for attempt in range(1, MAX_RETRIES + 1):
            headers = self._sign_headers(method, signed_path, body_str)
            try:
                resp: Response = self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    data=body_str or None,
                    timeout=15,
                )
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", RETRY_BACKOFF * attempt))
                    logger.warning("Rate limited — sleeping %.1fs", retry_after)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return resp.json()  # type: ignore[no-any-return]
            except requests.exceptions.RequestException as exc:
                if attempt == MAX_RETRIES:
                    raise
                sleep = RETRY_BACKOFF * attempt
                logger.warning("Request failed (attempt %d/%d): %s — retrying in %.1fs",
                               attempt, MAX_RETRIES, exc, sleep)
                time.sleep(sleep)

        raise RuntimeError(f"All {MAX_RETRIES} attempts failed for {method} {path}")

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> AccountInfo:
        data = self._request("GET", "/api/v1/crypto/trading/accounts/")
        results = data.get("results", [])
        acct = results[0] if results else data
        return AccountInfo(
            account_number=acct.get("account_number", ""),
            buying_power=float(acct.get("buying_power", 0)),
            currency=acct.get("currency_code", "USD"),
        )

    def get_holdings(self) -> list[Holding]:
        # v1 does not take account_number — filter by asset_code if needed
        data = self._request("GET", "/api/v1/crypto/trading/holdings/")
        results = data.get("results", [])
        holdings = []
        for h in results:
            qty = float(h.get("total_quantity", 0))
            if qty > 0:
                holdings.append(Holding(
                    asset_code=h.get("asset_code", ""),
                    total_quantity=qty,
                    quantity_available=float(h.get("quantity_available_for_trading", qty)),
                ))
        return holdings

    def get_holding(self, asset: str) -> Holding | None:
        for h in self.get_holdings():
            if h.asset_code == asset:
                return h
        return None

    # ── Market data ───────────────────────────────────────────────────────────

    def get_best_bid_ask(self, symbol: str = "BTC-USD") -> tuple[float, float]:
        data = self._request("GET", "/api/v1/crypto/marketdata/best_bid_ask/",
                             params={"symbol": symbol})
        results = data.get("results", [data])
        item = results[0] if results else data
        bid = float(item.get("bid_inclusive_of_sell_spread", item.get("bid", 0)))
        ask = float(item.get("ask_inclusive_of_buy_spread", item.get("ask", 0)))
        return bid, ask

    def get_mid_price(self, symbol: str = "BTC-USD") -> float:
        bid, ask = self.get_best_bid_ask(symbol)
        return (bid + ask) / 2

    def get_estimated_price(self, symbol: str, side: str, quantity: float) -> float:
        data = self._request(
            "GET",
            "/api/v1/crypto/marketdata/estimated_price/",
            params={"symbol": symbol, "side": side, "quantity": f"{quantity:.8f}"},
        )
        results = data.get("results", [data])
        return float(results[0].get("price", 0))

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_market_order(
        self,
        symbol: str,
        side: str,          # "buy" | "sell"
        asset_quantity: float,
    ) -> OrderResult:
        payload: dict[str, Any] = {
            "client_order_id": str(uuid.uuid4()),
            "side": side,
            "symbol": symbol,
            "type": "market",
            "market_order_config": {
                "asset_quantity": asset_quantity,
            },
        }
        data = self._request("POST", "/api/v1/crypto/trading/orders/", payload=payload)
        return _parse_order(data)

    def get_order(self, order_id: str) -> OrderResult:
        data = self._request("GET", f"/api/v1/crypto/trading/orders/{order_id}/")
        return _parse_order(data)

    def cancel_order(self, order_id: str) -> bool:
        try:
            self._request("POST", f"/api/v1/crypto/trading/orders/{order_id}/cancel/")
            return True
        except Exception as exc:
            logger.warning("Cancel order %s failed: %s", order_id, exc)
            return False

    # ── Convenience ───────────────────────────────────────────────────────────

    def buy_usd_amount(self, symbol: str, usd_amount: float) -> OrderResult:
        _bid, ask = self.get_best_bid_ask(symbol)
        asset = symbol.split("-")[0]
        quantity = usd_amount / ask
        logger.info("BUY %.8f %s @ ~$%.2f (ask) = $%.2f", quantity, asset, ask, usd_amount)
        return self.place_market_order(symbol, "buy", quantity)

    def sell_asset_amount(self, symbol: str, asset_quantity: float) -> OrderResult:
        bid, _ask = self.get_best_bid_ask(symbol)
        asset = symbol.split("-")[0]
        logger.info("SELL %.8f %s @ ~$%.2f (bid)", asset_quantity, asset, bid)
        return self.place_market_order(symbol, "sell", asset_quantity)


def _parse_order(data: dict[str, Any]) -> OrderResult:
    filled_qty = float(data.get("filled_asset_quantity", 0))
    avg_price = float(data.get("average_price") or 0)

    if filled_qty == 0 and data.get("executions"):
        executions = data["executions"]
        filled_qty = sum(float(e.get("quantity", 0)) for e in executions)
        total_notional = sum(
            float(e.get("effective_price", 0)) * float(e.get("quantity", 0))
            for e in executions
        )
        avg_price = total_notional / filled_qty if filled_qty else 0.0

    return OrderResult(
        order_id=data.get("id", ""),
        client_order_id=data.get("client_order_id", ""),
        state=data.get("state", "unknown"),
        side=data.get("side", ""),
        symbol=data.get("symbol", ""),
        filled_quantity=filled_qty,
        average_price=avg_price,
    )
