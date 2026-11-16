"""Fail-closed local handoff for server-verified KIS PAPER buy signals.

This client sends only a market and symbol to Spring. It never calls KIS directly,
chooses an order quantity, or retries a write with an unknown outcome.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

Market = Literal["KR", "US"]


class PaperSignalOrderError(RuntimeError):
    pass


class PaperSignalOrderOutcomeUnknown(PaperSignalOrderError):
    """Spring may have received the signal; reconcile the idempotency key before acting."""


def paper_signal_idempotency_key(
    *, market: Market, symbol: str, exchange: str | None, bar_timestamp: datetime,
) -> UUID:
    """Return a stable per-symbol, per-closed-bar key across restarts and model changes."""
    if bar_timestamp.tzinfo is None or bar_timestamp.utcoffset() is None:
        raise ValueError("PAPER signal bar timestamp must be timezone-aware")
    normalized_market = market.strip().upper()
    normalized_symbol = symbol.strip().upper()
    normalized_exchange = exchange.strip().upper() if exchange else ""
    if normalized_market not in {"KR", "US"} or not normalized_symbol:
        raise ValueError("PAPER signal identity is invalid")
    if (normalized_market == "US") != bool(normalized_exchange):
        raise ValueError("PAPER signal exchange must be present for US and absent for KR")
    bar_utc = bar_timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds")
    identity = "|".join((normalized_market, normalized_symbol, normalized_exchange, bar_utc))
    return uuid5(NAMESPACE_URL, f"ai-stock-trader-kis-paper-signal:{identity}")


def paper_signal_submission_enabled() -> bool:
    """Require all Python and Spring-side KIS PAPER gates before any write request."""
    expected = {
        "TRADING_MODE": "paper",
        "LIVE_TRADING_ENABLED": "false",
        "KIS_ENVIRONMENT": "paper",
        "KIS_MARKET_DATA_ENABLED": "true",
        "KIS_ACCOUNT_READ_ENABLED": "true",
        "KIS_PAPER_ACCOUNT_READ_ENABLED": "true",
        "KIS_PAPER_ORDERS_ENABLED": "true",
        "KIS_PAPER_RECONCILIATION_ENABLED": "true",
        "KIS_PAPER_SIGNAL_SUBMISSION_ENABLED": "true",
        "KIS_PAPER_CANCEL_PREFLIGHT_VERIFIED": "true",
        "KIS_PAPER_PROTECTIVE_EXIT_VERIFIED": "true",
    }
    return all(os.getenv(name, "").strip().lower() == value for name, value in expected.items())


class SpringKisPaperSignalOrderClient:
    """Send a vetted signal reference to the guarded local Spring PAPER endpoint."""

    def __init__(self, base_url: str = "http://127.0.0.1:18080", *, http_client: httpx.Client | None = None):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("KIS PAPER signal handoff must use a local Spring HTTP endpoint")
        self.base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(5.0, connect=1.0))
        self._owns_http = http_client is None

    def submit_signal(
        self, *, idempotency_key: UUID, market: Market, symbol: str, exchange: str | None = None,
    ) -> dict[str, object]:
        if not paper_signal_submission_enabled():
            raise PaperSignalOrderError("KIS PAPER signal submission is locked by local safety settings")
        normalized_market = market.strip().upper()
        normalized_symbol = symbol.strip().upper()
        normalized_exchange = exchange.strip().upper() if exchange else None
        if normalized_market not in {"KR", "US"} or not normalized_symbol:
            raise ValueError("PAPER signal market and symbol are invalid")
        if normalized_market == "US" or normalized_exchange is not None:
            raise ValueError("KIS PAPER signal submission is currently limited to domestic stocks")

        try:
            response = self._http.post(
                f"{self.base_url}/api/v1/kis/orders/paper/signals",
                headers={"Idempotency-Key": str(idempotency_key)},
                json={"market": normalized_market, "symbol": normalized_symbol},
            )
        except httpx.HTTPError as exc:
            raise PaperSignalOrderOutcomeUnknown(
                "KIS PAPER signal outcome is unknown; reconcile the idempotency key before acting"
            ) from exc
        if response.status_code >= 500:
            raise PaperSignalOrderOutcomeUnknown(
                "Spring server outcome is unknown; reconcile the idempotency key before acting"
            )
        if response.status_code not in {200, 202, 422}:
            raise PaperSignalOrderError(f"Spring rejected KIS PAPER signal (HTTP {response.status_code})")
        try:
            result = response.json()
        except ValueError as exc:
            if response.status_code in {200, 202}:
                raise PaperSignalOrderOutcomeUnknown(
                    "Spring response was unreadable; reconcile the idempotency key before acting"
                ) from exc
            raise PaperSignalOrderError("Spring returned an unreadable PAPER signal rejection") from exc
        if not isinstance(result, dict):
            raise PaperSignalOrderOutcomeUnknown("Spring response shape is unknown; reconcile before acting")
        return result

    def close(self) -> None:
        if self._owns_http:
            self._http.close()
