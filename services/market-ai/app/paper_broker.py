"""Spring paper-ledger adapter. It has no KIS order route and never retries writes."""

from __future__ import annotations

from decimal import Decimal
from datetime import date
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
        strategy_id: str | None = None, model_id: str | None = None,
    ) -> dict:
        if quantity <= 0 or limit_price <= 0:
            raise ValueError("paper order quantity and limit price must be positive")
        try:
            payload = {"market": market, "symbol": symbol, "side": side, "quantity": quantity,
                       "limitPrice": str(limit_price), "currency": currency}
            if strategy_id is not None:
                payload["strategyId"] = strategy_id
            if model_id is not None:
                payload["modelId"] = model_id
            response = self._http.post(
                f"{self.base_url}/api/v1/orders/paper",
                headers={"Idempotency-Key": str(idempotency_key)},
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise PaperBrokerOutcomeUnknown("paper order outcome is unknown; reconcile by idempotency key") from exc
        except httpx.HTTPError as exc:
            raise PaperBrokerOutcomeUnknown(
                "paper order outcome is unknown; reconcile by idempotency key before acting"
            ) from exc
        if response.status_code >= 500:
            raise PaperBrokerOutcomeUnknown(
                "paper order outcome is unknown; reconcile by idempotency key before acting"
            )
        if response.status_code not in {200, 201}:
            raise PaperBrokerError(f"Spring paper order rejected (HTTP {response.status_code})")
        try:
            return response.json()
        except ValueError as exc:
            raise PaperBrokerOutcomeUnknown("paper order response was unreadable; reconcile before retry") from exc

    def record_fill(
        self, order_id: int, *, fill_key: UUID, quantity: int, fill_price: Decimal,
        stop_price: Decimal | None = None, target_price: Decimal | None = None,
    ) -> dict:
        if order_id <= 0 or quantity <= 0 or fill_price <= 0:
            raise ValueError("paper fill identifiers and values must be positive")
        try:
            payload = {"fillKey": str(fill_key), "quantity": quantity, "fillPrice": str(fill_price)}
            if stop_price is not None or target_price is not None:
                if stop_price is None or target_price is None or not (Decimal(0) < stop_price < fill_price < target_price):
                    raise ValueError("strategy stop and target prices must surround the entry fill price")
                payload.update({"stopPrice": str(stop_price), "targetPrice": str(target_price)})
            response = self._http.post(
                f"{self.base_url}/api/v1/orders/paper/{order_id}/fills",
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise PaperBrokerOutcomeUnknown("paper fill outcome is unknown; reconcile before retry") from exc
        except httpx.HTTPError as exc:
            raise PaperBrokerOutcomeUnknown(
                "paper fill outcome is unknown; reconcile the order before acting"
            ) from exc
        if response.status_code >= 500:
            raise PaperBrokerOutcomeUnknown(
                "paper fill outcome is unknown; reconcile the order before acting"
            )
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

    def get_strategy_positions(self) -> list[dict]:
        """Read persistent local-paper exit levels from Spring; never queries KIS."""
        try:
            response = self._http.get(f"{self.base_url}/api/v1/orders/paper/strategy-risks")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                raise ValueError("strategy risk response has an invalid shape")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise PaperBrokerError("local paper exit state is unavailable") from exc

    def get_positions(self) -> list[dict]:
        return self._get_list("/api/v1/orders/paper/positions", "local paper positions are unavailable")

    def get_cash_balances(self) -> list[dict]:
        return self._get_list("/api/v1/orders/paper/cash", "local paper cash is unavailable")

    def get_operations_summary(self) -> dict:
        try:
            response = self._http.get(f"{self.base_url}/api/v1/operations/summary")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("consecutiveLosingTrades"), int):
                raise ValueError("operations summary has an invalid shape")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise PaperBrokerError("local paper risk summary is unavailable") from exc

    def get_watchlist_protection_state(self) -> dict[str, object]:
        """Read local PAPER positions and open-order count before dropping subscriptions."""
        try:
            response = self._http.get(f"{self.base_url}/api/v1/operations/summary")
            response.raise_for_status()
            payload = response.json()
            open_orders = payload.get("openOrders") if isinstance(payload, dict) else None
            positions = payload.get("positions") if isinstance(payload, dict) else None
            if type(open_orders) is not int or open_orders < 0 or not isinstance(positions, list):
                raise ValueError("paper watchlist protection state has an invalid shape")
            held: set[tuple[str, str]] = set()
            for position in positions:
                if not isinstance(position, dict):
                    raise ValueError("paper position has an invalid shape")
                market = str(position.get("market", "")).upper()
                symbol = str(position.get("symbol", "")).strip().upper()
                quantity = position.get("quantity")
                if market not in {"KR", "US"} or not symbol or type(quantity) is not int or quantity < 0:
                    raise ValueError("paper position has invalid identifiers")
                if quantity > 0:
                    held.add((market, symbol))
            return {"open_orders": open_orders, "held_symbols": held}
        except (httpx.HTTPError, ValueError) as exc:
            raise PaperBrokerError("paper watchlist protection state is unavailable") from exc

    def get_daily_risk_baselines(self, session_date: date) -> list[dict]:
        return self._get_list(
            f"/api/v1/orders/paper/risk-baselines/{session_date.isoformat()}",
            "local paper daily risk baseline is unavailable",
        )

    def record_daily_risk_baseline(self, session_date: date, currency: str, equity: Decimal) -> dict:
        if currency not in {"KRW", "USD"} or equity <= 0:
            raise ValueError("paper daily baseline currency and equity are invalid")
        try:
            response = self._http.put(
                f"{self.base_url}/api/v1/orders/paper/risk-baselines/{session_date.isoformat()}/{currency}",
                json={"equity": str(equity)},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("daily baseline response has an invalid shape")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise PaperBrokerError("local paper daily baseline could not be recorded") from exc

    def _get_list(self, path: str, error: str) -> list[dict]:
        try:
            response = self._http.get(f"{self.base_url}{path}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                raise ValueError("paper ledger response has an invalid shape")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise PaperBrokerError(error) from exc

    def close(self) -> None:
        if self._owns_http:
            self._http.close()
