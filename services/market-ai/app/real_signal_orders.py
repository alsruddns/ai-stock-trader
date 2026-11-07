"""Fail-closed local Spring handoff for server-verified REAL buy signals.

This client never calls KIS directly and never retries a write whose outcome may be unknown.
"""

from __future__ import annotations

import os
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

import httpx

Market = Literal["KR", "US"]
_US_EXCHANGES = {"NAS", "NASD", "NYS", "NYSE", "AMS", "AMEX"}


class RealSignalOrderError(RuntimeError):
    pass


class RealSignalOrderOutcomeUnknown(RealSignalOrderError):
    """The signal write may have reached Spring; reconcile the idempotency key before acting."""


def real_signal_submission_enabled() -> bool:
    """Require the dedicated handoff opt-in and all Spring REAL order/account gates."""
    expected = {
        "TRADING_MODE": "real",
        "LIVE_TRADING_ENABLED": "true",
        "KIS_REAL_ORDERS_ENABLED": "true",
        "KIS_ACCOUNT_READ_ENABLED": "true",
        "KIS_REAL_ACCOUNT_READ_ENABLED": "true",
        # Entries remain blocked until a restart-safe protective exit manager exists.
        "REAL_PROTECTIVE_EXIT_MANAGER_ENABLED": "true",
        # The manager must be actively enabled and known orders must be reconciled.
        "KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED": "true",
        "KIS_REAL_RECONCILIATION_ENABLED": "true",
        "REAL_SIGNAL_SUBMISSION_ENABLED": "true",
    }
    return all(os.getenv(name, "").strip().lower() == value for name, value in expected.items())


class SpringRealSignalOrderClient:
    """Submit only a market/symbol signal reference to Spring's guarded REAL API."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080", *, http_client: httpx.Client | None = None):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("REAL signal handoff must use a local Spring HTTP endpoint")
        self.base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(5.0, connect=1.0))
        self._owns_http = http_client is None

    def submit_signal(
        self, *, idempotency_key: UUID, market: Market, symbol: str, exchange: str | None = None,
    ) -> dict[str, object]:
        if not real_signal_submission_enabled():
            raise RealSignalOrderError("REAL signal submission is locked by local safety settings")
        normalized_market = market.strip().upper()
        normalized_symbol = symbol.strip().upper()
        normalized_exchange = exchange.strip().upper() if exchange else None
        if normalized_market not in {"KR", "US"} or not normalized_symbol:
            raise ValueError("REAL signal market and symbol are invalid")
        if normalized_market == "US" and normalized_exchange not in _US_EXCHANGES:
            raise ValueError("a supported US exchange is required for a REAL signal")
        if normalized_market == "KR" and normalized_exchange is not None:
            raise ValueError("KR REAL signals must not include a US exchange")

        params = {"exchange": normalized_exchange} if normalized_exchange else None
        payload = {"market": normalized_market, "symbol": normalized_symbol}
        try:
            response = self._http.post(
                f"{self.base_url}/api/v1/kis/orders/real/signals",
                params=params,
                headers={"Idempotency-Key": str(idempotency_key)},
                json=payload,
            )
        except httpx.HTTPError as exc:
            # Conservatively treat transport failures as unknown; do not replay an order signal.
            raise RealSignalOrderOutcomeUnknown(
                "REAL signal outcome is unknown; reconcile the idempotency key before acting"
            ) from exc
        if response.status_code not in {200, 202, 422}:
            raise RealSignalOrderError(f"Spring rejected REAL signal submission (HTTP {response.status_code})")
        try:
            result = response.json()
        except ValueError as exc:
            if response.status_code in {200, 202}:
                raise RealSignalOrderOutcomeUnknown(
                    "REAL signal response was unreadable; reconcile the idempotency key before acting"
                ) from exc
            raise RealSignalOrderError("Spring returned an unreadable REAL signal rejection") from exc
        if not isinstance(result, dict):
            raise RealSignalOrderOutcomeUnknown("REAL signal response shape is unknown; reconcile before acting")
        return result

    def close(self) -> None:
        if self._owns_http:
            self._http.close()
