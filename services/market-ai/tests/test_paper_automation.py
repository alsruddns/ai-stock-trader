from datetime import datetime, timezone
from decimal import Decimal

from app.paper_automation import PaperAutoTrader, PaperEntryDecision, PaperAutomationReadiness
from app.paper_broker import PaperBrokerOutcomeUnknown
from app.paper_ops import PaperGateState, PaperOrderSizing


class FakeBroker:
    def __init__(self):
        self.order = None
        self.submit_calls = 0
        self.fill_calls = 0

    def get_order_by_idempotency_key(self, _key):
        return self.order

    def submit_order(self, **kwargs):
        self.submit_calls += 1
        self.order = {"id": 17, "status": "PENDING", "filledQuantity": 0,
                      "market": kwargs["market"], "symbol": kwargs["symbol"], "side": kwargs["side"],
                      "quantity": kwargs["quantity"], "limit_price": kwargs["limit_price"],
                      "currency": kwargs["currency"]}
        return self.order

    def record_fill(self, order_id, *, fill_key, quantity, fill_price, stop_price=None, target_price=None):
        self.fill_calls += 1
        self.order.update({"id": order_id, "status": "FILLED", "filledQuantity": quantity,
                           "fillKey": fill_key, "fillPrice": fill_price,
                           "stopPrice": stop_price, "targetPrice": target_price})
        return self.order


def _decision(*, exit_ready: bool = False, allowed: bool = True) -> PaperEntryDecision:
    return PaperEntryDecision(
        market="KR", symbol="005930", currency="KRW", signal="BUY_CANDIDATE", model_approved=True,
        gate=PaperGateState("KR", True, False, allowed, True, ()),
        sizing=PaperOrderSizing(2, Decimal("200000"), Decimal("100"), Decimal("500")),
        bar_timestamp=datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc),
        limit_price=Decimal("100000"), stop_price=Decimal("99000"), target_price=Decimal("102000"),
        exit_manager_ready=exit_ready,
    )


def _enable_paper_automation(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("KIS_PAPER_ORDERS_ENABLED", "false")


def test_automation_defaults_locked_and_does_not_touch_broker(monkeypatch) -> None:
    monkeypatch.delenv("PAPER_AUTOTRADING_ENABLED", raising=False)
    broker = FakeBroker()

    result = PaperAutoTrader(broker).submit_entry(_decision(exit_ready=True))

    assert result["reason"] == "PAPER_AUTOMATION_LOCKED"
    assert broker.submit_calls == broker.fill_calls == 0


def test_automation_requires_persistent_exit_management_even_when_opted_in(monkeypatch) -> None:
    _enable_paper_automation(monkeypatch)
    broker = FakeBroker()

    result = PaperAutoTrader(broker).submit_entry(_decision(exit_ready=False))

    assert result["reason"] == "PERSISTENT_EXIT_MANAGER_REQUIRED"
    assert broker.submit_calls == broker.fill_calls == 0


def test_automation_uses_local_paper_order_and_deterministic_fill_once(monkeypatch) -> None:
    _enable_paper_automation(monkeypatch)
    broker = FakeBroker()
    trader = PaperAutoTrader(broker)

    first = trader.submit_entry(_decision(exit_ready=True))
    second = trader.submit_entry(_decision(exit_ready=True))

    assert first["state"] == "FILLED"
    assert second["state"] == "FILLED"
    assert first["idempotency_key"] == second["idempotency_key"]
    assert broker.submit_calls == 1
    assert broker.fill_calls == 1
    assert broker.order["stopPrice"] == Decimal("99000")
    assert broker.order["targetPrice"] == Decimal("102000")


def test_kis_paper_order_switch_blocks_local_automation(monkeypatch) -> None:
    _enable_paper_automation(monkeypatch)
    monkeypatch.setenv("KIS_PAPER_ORDERS_ENABLED", "true")

    readiness = PaperAutomationReadiness.from_environment()

    assert readiness.enabled is False


def test_ambiguous_paper_order_result_is_not_retried(monkeypatch) -> None:
    _enable_paper_automation(monkeypatch)

    class UnknownBroker(FakeBroker):
        def submit_order(self, **kwargs):
            self.submit_calls += 1
            raise PaperBrokerOutcomeUnknown("unknown")

    broker = UnknownBroker()
    result = PaperAutoTrader(broker).submit_entry(_decision(exit_ready=True))

    assert result["state"] == "UNKNOWN"
    assert result["reason"] == "RECONCILIATION_REQUIRED"
    assert broker.submit_calls == 1
    assert broker.fill_calls == 0


def test_existing_idempotency_record_with_changed_order_terms_is_not_filled(monkeypatch) -> None:
    _enable_paper_automation(monkeypatch)
    broker = FakeBroker()
    broker.order = {"id": 18, "status": "PENDING", "filledQuantity": 0, "market": "KR",
                    "symbol": "000660", "side": "BUY", "quantity": 2,
                    "limitPrice": "100000", "currency": "KRW"}

    result = PaperAutoTrader(broker).submit_entry(_decision(exit_ready=True))

    assert result["reason"] == "IDEMPOTENCY_ORDER_MISMATCH"
    assert broker.submit_calls == broker.fill_calls == 0
