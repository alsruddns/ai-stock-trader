from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from app.paper_broker import PaperBrokerOutcomeUnknown, SpringPaperBrokerClient


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
    )
    assert result["id"] == 42
    assert seen[0].url.path == "/api/v1/orders/paper"
    assert seen[0].headers["idempotency-key"] == "d8d449d8-f154-4c5a-b1d0-6ce69f790f51"
    assert seen[0].read().decode().find("70000") >= 0
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


def test_broker_adapter_refuses_nonlocal_hosts() -> None:
    with pytest.raises(ValueError, match="local"):
        SpringPaperBrokerClient("http://example.com")


def test_lookup_by_idempotency_key_supports_safe_reconciliation() -> None:
    key = UUID(int=5)
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    broker = SpringPaperBrokerClient(http_client=http)
    assert broker.get_order_by_idempotency_key(key) is None
    http.close()
