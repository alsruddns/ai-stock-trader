"""Spring paper-ledger adapter. It has no KIS order route and never retries writes."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

import httpx

from .quant import Market

Side = Literal["BUY", "SELL"]


class PaperBrokerError(RuntimeError):
    pass


class PaperBrokerOutcomeUnknown(PaperBrokerError):
    """A write timed out after it may have reached Spring; reconcile before any retry."""


class SpringPaperBrokerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8080", *, http_client: httpx.Client | None = None):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("paper broker must use a local HTTP endpoint")
        self.base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(3.0, connect=1.0))
        self._owns_http = http_client is None

    def submit_order(
        self, *, idempotency_key: UUID, market: Market, symbol: str, side: Side,
        quantity: int, limit_price: Decimal, currency: Literal["KRW", "USD"],
    ) -> dict:
        if quantity <= 0 or limit_price <= 0:
            raise ValueError("paper order quantity and limit price must be positive")
        try:
            response = self._http.post(
                f"{self.base_url}/api/v1/orders/paper",
                headers={"Idempotency-Key": str(idempotency_key)},
                json={"market": market, "symbol": symbol, "side": side, "quantity": quantity,
                      "limitPrice": str(limit_price), "currency": currency},
            )
        except httpx.TimeoutException as exc:
            raise PaperBrokerOutcomeUnknown("paper order outcome is unknown; reconcile by idempotency key") from exc
        except httpx.HTTPError as exc:
            raise PaperBrokerError("paper order request failed") from exc
        if response.status_code not in {200, 201}:
            raise PaperBrokerError(f"Spring paper order rejected (HTTP {response.status_code})")
        try:
            return response.json()
        except ValueError as exc:
            raise PaperBrokerOutcomeUnknown("paper order response was unreadable; reconcile before retry") from exc

    def record_fill(self, order_id: int, *, fill_key: UUID, quantity: int, fill_price: Decimal) -> dict:
        if order_id <= 0 or quantity <= 0 or fill_price <= 0:
            raise ValueError("paper fill identifiers and values must be positive")
        try:
            response = self._http.post(
                f"{self.base_url}/api/v1/orders/paper/{order_id}/fills",
                json={"fillKey": str(fill_key), "quantity": quantity, "fillPrice": str(fill_price)},
            )
        except httpx.TimeoutException as exc:
            raise PaperBrokerOutcomeUnknown("paper fill outcome is unknown; reconcile before retry") from exc
        except httpx.HTTPError as exc:
            raise PaperBrokerError("paper fill request failed") from exc
        if response.status_code not in {200, 201}:
            raise PaperBrokerError(f"Spring paper fill rejected (HTTP {response.status_code})")
        try:
            return response.json()
        except ValueError as exc:
            raise PaperBrokerOutcomeUnknown("paper fill response was unreadable; reconcile before retry") from exc

    def get_order(self, order_id: int) -> dict:
        try:
            response = self._http.get(f"{self.base_url}/api/v1/orders/paper/{order_id}")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PaperBrokerError("paper order reconciliation failed") from exc

    def get_order_by_idempotency_key(self, key: UUID) -> dict | None:
        try:
            response = self._http.get(f"{self.base_url}/api/v1/orders/paper/by-idempotency/{key}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PaperBrokerError("paper order reconciliation failed") from exc

    def close(self) -> None:
        if self._owns_http:
            self._http.close()
