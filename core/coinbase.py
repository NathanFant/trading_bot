"""
Coinbase Advanced Trade API client (App API key auth, ES256 JWT).

Key format note: .env.local stores the private key as raw base64 with literal
\n sequences (not real newlines). _load_private_key() handles that normalization.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass

import jwt
import requests
from requests.exceptions import ConnectionError, Timeout, HTTPError
from cryptography.hazmat.primitives.serialization import load_pem_private_key

_BASE = "https://api.coinbase.com"
_HOST = "api.coinbase.com"


@dataclass
class CBAccount:
    uuid: str
    currency: str
    available: float
    hold: float


@dataclass
class PerpSnapshot:
    product_id: str
    asset: str
    contract_size: float
    price: float
    contract_value_usd: float
    intraday_long_leverage: float
    intraday_short_leverage: float
    overnight_long_leverage: float
    overnight_short_leverage: float
    min_margin_long_usd: float   # margin needed for 1 contract at overnight long rate
    min_margin_short_usd: float
    funding_rate_pct_per_hr: float
    funding_rate_apr_pct: float
    open_interest_contracts: int
    open_interest_usd: float
    index_price: float
    settlement_price: float
    funding_time: str
    snapshot_ts: str             # ISO UTC timestamp when this was fetched


@dataclass
class CBOrder:
    order_id: str
    status: str
    filled_value: float
    filled_size: float
    average_filled_price: float


def _load_private_key(raw: str):
    """Convert stored key (raw base64 with literal \\n) to a cryptography key object."""
    cleaned = raw.replace("\\n", "\n")
    if "-----" not in cleaned:
        pem = f"-----BEGIN EC PRIVATE KEY-----\n{cleaned}\n-----END EC PRIVATE KEY-----\n"
    else:
        pem = cleaned
    return load_pem_private_key(pem.encode(), password=None)


class CoinbaseClient:
    def __init__(self, api_key_name: str | None = None, private_key_raw: str | None = None) -> None:
        self._key_name = api_key_name or os.environ["COINBASE_API_KEY_NAME"]
        raw = private_key_raw or os.environ["COINBASE_API_PRIVATE_KEY"]
        self._private_key = _load_private_key(raw)

    def _token(self, method: str, path: str) -> str:
        uri = f"{method} {_HOST}{path}"
        payload = {
            "sub": self._key_name,
            "iss": "cdp",
            "nbf": int(time.time()),
            "exp": int(time.time()) + 120,
            "uri": uri,
        }
        return jwt.encode(
            payload,
            self._private_key,
            algorithm="ES256",
            headers={"kid": self._key_name, "nonce": uuid.uuid4().hex},
        )

    def _get(self, path: str, _retries: int = 3, _backoff: float = 0.5) -> dict:
        uri_path = path.split("?")[0]
        last_exc: Exception | None = None
        for attempt in range(_retries):
            try:
                token = self._token("GET", uri_path)
                r = requests.get(
                    f"{_BASE}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                # Don't retry client errors (4xx) — they won't self-heal
                if 400 <= r.status_code < 500:
                    r.raise_for_status()
                r.raise_for_status()
                return r.json()
            except (ConnectionError, Timeout) as exc:
                last_exc = exc
            except HTTPError as exc:
                if exc.response is not None and exc.response.status_code < 500:
                    raise
                last_exc = exc
            if attempt < _retries - 1:
                time.sleep(_backoff * (2 ** attempt))
        raise last_exc  # type: ignore[misc]

    def _post(self, path: str, body: dict, _retries: int = 3, _backoff: float = 0.5) -> dict:
        last_exc: Exception | None = None
        for attempt in range(_retries):
            try:
                token = self._token("POST", path)
                r = requests.post(
                    f"{_BASE}{path}",
                    json=body,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    timeout=10,
                )
                if 400 <= r.status_code < 500:
                    r.raise_for_status()
                r.raise_for_status()
                return r.json()
            except (ConnectionError, Timeout) as exc:
                last_exc = exc
            except HTTPError as exc:
                if exc.response is not None and exc.response.status_code < 500:
                    raise
                last_exc = exc
            if attempt < _retries - 1:
                time.sleep(_backoff * (2 ** attempt))
        raise last_exc  # type: ignore[misc]

    # ── Public ────────────────────────────────────────────────────────────────

    def get_accounts(self) -> list[CBAccount]:
        data = self._get("/api/v3/brokerage/accounts")
        return [
            CBAccount(
                uuid=a["uuid"],
                currency=a["currency"],
                available=float(a["available_balance"]["value"]),
                hold=float(a["hold"]["value"]),
            )
            for a in data.get("accounts", [])
        ]

    def get_account(self, currency: str) -> CBAccount | None:
        accounts = self.get_accounts()
        for a in accounts:
            if a.currency == currency and a.available > 0:
                return a
        return next((a for a in accounts if a.currency == currency), None)

    def get_usd_balance(self) -> float:
        acct = self.get_account("USD")
        return acct.available if acct else 0.0

    def get_best_bid_ask(self, product_id: str) -> tuple[float, float]:
        """Returns (best_bid, best_ask) for a product (e.g. 'SOL-USD')."""
        data = self._get(f"/api/v3/brokerage/best_bid_ask?product_ids={product_id}")
        for entry in data.get("pricebooks", []):
            if entry["product_id"] == product_id:
                bid = float(entry["bids"][0]["price"]) if entry.get("bids") else 0.0
                ask = float(entry["asks"][0]["price"]) if entry.get("asks") else 0.0
                return bid, ask
        raise ValueError(f"No price data for {product_id}")

    def get_mid_price(self, product_id: str) -> float:
        bid, ask = self.get_best_bid_ask(product_id)
        return (bid + ask) / 2

    # ── Perpetuals ────────────────────────────────────────────────────────────

    # Coinbase CDE perpetuals use far-dated expiry contracts (Dec 2030)
    PERP_PRODUCTS = {
        "SOL": "SLP-20DEC30-CDE",
        "BTC": "BIP-20DEC30-CDE",
        "ETH": "ETP-20DEC30-CDE",
    }

    def get_perp_snapshot(self, asset: str) -> PerpSnapshot:
        """Fetch live margin rates, funding, and contract details for a perp."""
        from datetime import datetime, timezone
        product_id = self.PERP_PRODUCTS[asset.upper()]
        p = self._get(f"/api/v3/brokerage/products/{product_id}")
        fd = p["future_product_details"]
        intra = fd["intraday_margin_rate"]
        over = fd["overnight_margin_rate"]

        contract_size = float(fd["contract_size"])
        price = float(p["price"])
        contract_value = contract_size * price

        over_long_lev = 1 / float(over["long_margin_rate"])
        over_short_lev = 1 / float(over["short_margin_rate"])
        intra_long_lev = 1 / float(intra["long_margin_rate"])
        intra_short_lev = 1 / float(intra["short_margin_rate"])
        funding_hr = float(fd.get("funding_rate", 0))

        return PerpSnapshot(
            product_id=product_id,
            asset=asset.upper(),
            contract_size=contract_size,
            price=price,
            contract_value_usd=contract_value,
            intraday_long_leverage=round(intra_long_lev, 2),
            intraday_short_leverage=round(intra_short_lev, 2),
            overnight_long_leverage=round(over_long_lev, 2),
            overnight_short_leverage=round(over_short_lev, 2),
            min_margin_long_usd=round(contract_value / over_long_lev, 2),
            min_margin_short_usd=round(contract_value / over_short_lev, 2),
            funding_rate_pct_per_hr=round(funding_hr * 100, 6),
            funding_rate_apr_pct=round(funding_hr * 100 * 24 * 365, 2),
            open_interest_contracts=int(fd.get("open_interest", 0)),
            open_interest_usd=round(int(fd.get("open_interest", 0)) * contract_value),
            index_price=float(fd.get("index_price", 0)),
            settlement_price=float(fd.get("settlement_price", 0)),
            funding_time=fd.get("funding_time", ""),
            snapshot_ts=datetime.now(timezone.utc).isoformat(),
        )

    def get_perp_candles(self, asset: str, days: int = 30) -> list[dict]:
        """Daily OHLCV candles for a perp product."""
        import time
        product_id = self.PERP_PRODUCTS[asset.upper()]
        end = int(time.time())
        start = end - days * 86400
        data = self._get(
            f"/api/v3/brokerage/products/{product_id}/candles"
            f"?start={start}&end={end}&granularity=ONE_DAY"
        )
        return sorted(data.get("candles", []), key=lambda c: int(c["start"]))

    def buy_usd_amount(self, product_id: str, usd_amount: float) -> CBOrder:
        """Market buy for a USD notional amount."""
        body = {
            "client_order_id": uuid.uuid4().hex,
            "product_id": product_id,
            "side": "BUY",
            "order_configuration": {
                "market_market_ioc": {
                    "quote_size": f"{usd_amount:.2f}",
                }
            },
        }
        data = self._post("/api/v3/brokerage/orders", body)
        return self._parse_order(data)

    def sell_asset_amount(self, product_id: str, base_size: float) -> CBOrder:
        """Market sell for a base asset quantity."""
        body = {
            "client_order_id": uuid.uuid4().hex,
            "product_id": product_id,
            "side": "SELL",
            "order_configuration": {
                "market_market_ioc": {
                    "base_size": f"{base_size:.8f}",
                }
            },
        }
        data = self._post("/api/v3/brokerage/orders", body)
        return self._parse_order(data)

    def get_order(self, order_id: str) -> CBOrder:
        data = self._get(f"/api/v3/brokerage/orders/historical/{order_id}")
        return self._parse_order({"order": data.get("order", {})})

    @staticmethod
    def _parse_order(data: dict) -> CBOrder:
        o = data.get("order", data.get("success_response", {}))
        if not o:
            o = data
        fills = o.get("order_configuration", {})
        return CBOrder(
            order_id=o.get("order_id", ""),
            status=o.get("status", ""),
            filled_value=float(o.get("filled_value", 0) or 0),
            filled_size=float(o.get("filled_size", 0) or 0),
            average_filled_price=float(o.get("average_filled_price", 0) or 0),
        )
