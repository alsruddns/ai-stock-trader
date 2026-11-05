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
        self.order = {"id": 17, "status": "PENDING", "filledQuantity": 0, **kwargs}
        return self.order

    def record_fill(self, order_id, *, fill_key, quantity, fill_price):
        self.fill_calls += 1
        self.order = {"id": order_id, "status": "FILLED", "filledQuantity": quantity,
                      "fillKey": fill_key, "fillPrice": fill_price}
        return self.order


def _decision(*, exit_ready: bool = False, allowed: bool = True) -> PaperEntryDecision:
    return PaperEntryDecision(
        market="KR", symbol="005930", currency="KRW", signal="BUY_CANDIDATE", model_approved=True,
        gate=PaperGateState("KR", True, False, allowed, True, ()),
        sizing=PaperOrderSizing(2, Decimal("200000"), Decimal("100"), Decimal("500")),
        bar_timestamp=datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc),
        limit_price=Decimal("100000"), exit_manager_ready=exit_ready,
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
