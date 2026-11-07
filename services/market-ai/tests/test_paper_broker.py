from decimal import Decimal
from datetime import date
from uuid import UUID

import httpx
import pytest

from app.paper_broker import PaperBrokerError, PaperBrokerOutcomeUnknown, SpringPaperBrokerClient


def test_paper_order_adapter_uses_idempotency_key_and_local_route() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": 42, "status": "PENDING"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    broker = SpringPaperBrokerClient(http_client=client)
    result = broker.submit_order(
        idempotency_key=UUID("d8d449d8-f154-4c5a-b1d0-6ce69f790f51"), market="KR",
        symbol="005930", side="BUY", quantity=1, limit_price=Decimal("70000"), currency="KRW",
        strategy_id="ORB_VWAP", model_id="lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert result["id"] == 42
    assert seen[0].url.path == "/api/v1/orders/paper"
    assert seen[0].headers["idempotency-key"] == "d8d449d8-f154-4c5a-b1d0-6ce69f790f51"
    assert seen[0].read().decode().find("70000") >= 0
    assert '"strategyId":"ORB_VWAP"' in seen[0].read().decode()
    assert '"modelId":"lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' in seen[0].read().decode()
    client.close()


def test_ambiguous_timeout_is_not_retried_and_requires_reconciliation() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("sensitive upstream details", request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    broker = SpringPaperBrokerClient(http_client=http)
    with pytest.raises(PaperBrokerOutcomeUnknown, match="reconcile"):
        broker.submit_order(
            idempotency_key=UUID(int=1), market="US", symbol="AAPL", side="BUY", quantity=1,
            limit_price=Decimal("100"), currency="USD",
        )
    assert calls == 1
    http.close()


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_server_errors_after_order_post_require_idempotency_reconciliation(status_code: int) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json={"detail": "sensitive server response"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    broker = SpringPaperBrokerClient(http_client=http)
    with pytest.raises(PaperBrokerOutcomeUnknown, match="idempotency key"):
        broker.submit_order(
            idempotency_key=UUID(int=101), market="KR", symbol="005930", side="BUY", quantity=1,
            limit_price=Decimal("70000"), currency="KRW",
        )
    assert calls == 1
    http.close()


def test_server_error_after_fill_post_requires_order_reconciliation() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"detail": "sensitive server response"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    broker = SpringPaperBrokerClient(http_client=http)
    with pytest.raises(PaperBrokerOutcomeUnknown, match="reconcile the order"):
        broker.record_fill(42, fill_key=UUID(int=102), quantity=1, fill_price=Decimal("70000"))
    assert calls == 1
    http.close()


def test_broker_adapter_refuses_nonlocal_hosts() -> None:
    with pytest.raises(ValueError, match="local"):
        SpringPaperBrokerClient("http://example.com")


def test_lookup_by_idempotency_key_supports_safe_reconciliation() -> None:
    key = UUID(int=5)
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    broker = SpringPaperBrokerClient(http_client=http)
    assert broker.get_order_by_idempotency_key(key) is None
    http.close()


def test_fill_adapter_includes_atomic_strategy_exit_levels() -> None:
    seen: list[httpx.Request] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: (
        seen.append(request) or httpx.Response(200, json={"id": 12, "status": "FILLED"})
    )))
    broker = SpringPaperBrokerClient(http_client=client)
    broker.record_fill(12, fill_key=UUID(int=9), quantity=2, fill_price=Decimal("100"),
                       stop_price=Decimal("98"), target_price=Decimal("104"))

    assert seen[0].read().decode().find('"stopPrice":"98"') >= 0
    assert seen[0].read().decode().find('"targetPrice":"104"') >= 0
    client.close()


def test_strategy_position_reader_uses_local_paper_exit_endpoint() -> None:
    seen: list[httpx.Request] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: (
        seen.append(request) or httpx.Response(200, json=[{"market": "KR", "symbol": "005930", "quantity": 2}])
    )))
    broker = SpringPaperBrokerClient(http_client=client)

    positions = broker.get_strategy_positions()

    assert positions[0]["symbol"] == "005930"
    assert seen[0].url.path == "/api/v1/orders/paper/strategy-risks"
    client.close()


def test_daily_baseline_is_read_and_recorded_only_through_local_paper_routes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"currency": "KRW", "equity": "1000000"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    broker = SpringPaperBrokerClient(http_client=client)
    assert broker.get_daily_risk_baselines(date(2026, 11, 5)) == []
    assert broker.record_daily_risk_baseline(date(2026, 11, 5), "KRW", Decimal("1000000"))["currency"] == "KRW"
    assert seen[0].url.path.endswith("/risk-baselines/2026-11-05")
    assert seen[1].method == "PUT"
    assert seen[1].url.path.endswith("/risk-baselines/2026-11-05/KRW")
    assert seen[1].read().decode() == '{"equity":"1000000"}'
    client.close()


def test_operations_summary_exposes_only_local_paper_risk_counters() -> None:
    seen: list[httpx.Request] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: (
        seen.append(request) or httpx.Response(200, json={"consecutiveLosingTrades": 2, "openOrders": 0})
    )))
    broker = SpringPaperBrokerClient(http_client=client)
    assert broker.get_operations_summary()["consecutiveLosingTrades"] == 2
    assert seen[0].url.path == "/api/v1/operations/summary"
    client.close()


def test_watchlist_protection_state_requires_valid_positions_and_open_order_count() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "openOrders": 1,
        "positions": [
            {"market": "KR", "symbol": "005930", "quantity": 2},
            {"market": "US", "symbol": "AAPL", "quantity": 0},
        ],
    })))
    broker = SpringPaperBrokerClient(http_client=client)
    state = broker.get_watchlist_protection_state()
    assert state == {"open_orders": 1, "held_symbols": {("KR", "005930")}}
    client.close()


def test_watchlist_protection_state_fails_closed_on_malformed_ledger() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "openOrders": 0, "positions": [{"market": "KR", "symbol": "005930"}],
    })))
    broker = SpringPaperBrokerClient(http_client=client)
    with pytest.raises(PaperBrokerError, match="protection state is unavailable"):
        broker.get_watchlist_protection_state()
    client.close()
