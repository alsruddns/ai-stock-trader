"""Fail-closed coordinator for local Spring paper orders only.

This module is intentionally not connected to the KIS order client. It also requires
an explicit exit-manager readiness signal before accepting an entry candidate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from .paper_broker import PaperBrokerError, PaperBrokerOutcomeUnknown, SpringPaperBrokerClient
from .paper_ops import PaperGateState, PaperOrderSizing
from .quant import Market


@dataclass(frozen=True)
class PaperAutomationReadiness:
    enabled: bool
    trading_mode: str
    live_trading_enabled: bool
    kis_paper_orders_enabled: bool

    @classmethod
    def from_environment(cls) -> "PaperAutomationReadiness":
        mode = os.getenv("TRADING_MODE", "paper").strip().lower()
        live = os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower() == "true"
        kis_orders = os.getenv("KIS_PAPER_ORDERS_ENABLED", "false").strip().lower() == "true"
        explicit_opt_in = os.getenv("PAPER_AUTOTRADING_ENABLED", "false").strip().lower() == "true"
        return cls(explicit_opt_in and mode == "paper" and not live and not kis_orders,
                   mode, live, kis_orders)


@dataclass(frozen=True)
class PaperEntryDecision:
    market: Market
    symbol: str
    currency: str
    signal: str
    model_approved: bool
    gate: PaperGateState
    sizing: PaperOrderSizing | None
    bar_timestamp: datetime
    limit_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    exit_manager_ready: bool


class PaperAutoTrader:
    """Create at most one local paper fill per strategy bar after all safety gates pass."""

    def __init__(self, broker: SpringPaperBrokerClient | None = None):
        self._broker = broker or SpringPaperBrokerClient()
        self._owns_broker = broker is None

    def submit_entry(self, decision: PaperEntryDecision) -> dict[str, object]:
        readiness = PaperAutomationReadiness.from_environment()
        if not readiness.enabled:
            return {"state": "BLOCKED", "reason": "PAPER_AUTOMATION_LOCKED", "executable": False}
        if decision.signal != "BUY_CANDIDATE" or not decision.model_approved:
            return {"state": "BLOCKED", "reason": "APPROVED_AI_AND_QUANT_SIGNAL_REQUIRED", "executable": False}
        if not decision.gate.new_entries_allowed:
            return {"state": "BLOCKED", "reason": "PAPER_RISK_GATE_BLOCKED", "executable": False}
        if not decision.exit_manager_ready:
            return {"state": "BLOCKED", "reason": "PERSISTENT_EXIT_MANAGER_REQUIRED", "executable": False}
        if (decision.sizing is None or decision.sizing.quantity <= 0 or decision.limit_price <= 0
                or not (Decimal(0) < decision.stop_price < decision.limit_price < decision.target_price)):
            return {"state": "BLOCKED", "reason": "VALID_ORDER_SIZE_REQUIRED", "executable": False}

        symbol = decision.symbol.strip().upper()
        idempotency_key = uuid5(NAMESPACE_URL,
                                f"ai-stock-trader-paper:{decision.market}:{symbol}:{decision.bar_timestamp.isoformat()}")
        try:
            order = self._broker.get_order_by_idempotency_key(idempotency_key)
            if order is None:
                order = self._broker.submit_order(
                    idempotency_key=idempotency_key, market=decision.market, symbol=symbol, side="BUY",
                    quantity=decision.sizing.quantity, limit_price=decision.limit_price,
                    currency=decision.currency,
                )
            order_id = int(order["id"])
            existing_price = order.get("limitPrice", order.get("limit_price"))
            if (str(order.get("market", "")).upper() != decision.market
                    or str(order.get("symbol", "")).upper() != symbol
                    or str(order.get("side", "")).upper() != "BUY"
                    or int(order.get("quantity", -1)) != decision.sizing.quantity
                    or existing_price is None or Decimal(str(existing_price)) != decision.limit_price
                    or str(order.get("currency", "")).upper() != decision.currency):
                return {"state": "BLOCKED", "reason": "IDEMPOTENCY_ORDER_MISMATCH",
                        "order_id": order_id, "idempotency_key": str(idempotency_key), "executable": False}
            order_status = str(order.get("status", "")).upper()
            if order_status not in {"PENDING", "PARTIALLY_FILLED", "FILLED"}:
                return {"state": "BLOCKED", "reason": "ORDER_NOT_OPEN_FOR_PAPER_FILL",
                        "order_id": order_id, "idempotency_key": str(idempotency_key), "executable": False}
            filled = int(order.get("filledQuantity", 0))
            remaining = max(0, decision.sizing.quantity - filled)
            if remaining == 0:
                return {"state": order.get("status", "FILLED"), "order_id": order_id,
                        "idempotency_key": str(idempotency_key), "executable": False}
            fill_key = uuid5(NAMESPACE_URL, f"{idempotency_key}:paper-fill:{filled}:{remaining}")
            filled_order = self._broker.record_fill(order_id, fill_key=fill_key,
                                                    quantity=remaining, fill_price=decision.limit_price,
                                                    stop_price=decision.stop_price, target_price=decision.target_price)
            return {"state": filled_order.get("status", "FILLED"), "order_id": order_id,
                    "idempotency_key": str(idempotency_key), "filled_quantity": filled_order.get("filledQuantity", remaining),
                    "executable": False}
        except PaperBrokerOutcomeUnknown:
            # Do not retry an ambiguous create/fill. The next run first reconciles by key.
            return {"state": "UNKNOWN", "reason": "RECONCILIATION_REQUIRED",
                    "idempotency_key": str(idempotency_key), "executable": False}
        except PaperBrokerError:
            return {"state": "BLOCKED", "reason": "LOCAL_PAPER_BROKER_UNAVAILABLE",
                    "idempotency_key": str(idempotency_key), "executable": False}

    def close(self) -> None:
        if self._owns_broker:
            self._broker.close()
