from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from app.real_signal_orders import (
    RealSignalOrderError,
    RealSignalOrderOutcomeUnknown,
    SpringRealSignalOrderClient,
    real_signal_submission_enabled,
)


def enable_real_signal_gates(monkeypatch) -> None:
    for key, value in {
        "TRADING_MODE": "real",
        "LIVE_TRADING_ENABLED": "true",
        "KIS_REAL_ORDERS_ENABLED": "true",
        "KIS_ACCOUNT_READ_ENABLED": "true",
        "KIS_REAL_ACCOUNT_READ_ENABLED": "true",
        "REAL_PROTECTIVE_EXIT_MANAGER_ENABLED": "true",
        "KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED": "true",
        "KIS_REAL_RECONCILIATION_ENABLED": "true",
        "REAL_SIGNAL_SUBMISSION_ENABLED": "true",
    }.items():
        monkeypatch.setenv(key, value)


def test_real_signal_submission_is_locked_by_default_and_sends_no_request(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "paper")
    requests = []
    transport = httpx.MockTransport(lambda request: requests.append(request))
    client = SpringRealSignalOrderClient(http_client=httpx.Client(transport=transport))

    with pytest.raises(RealSignalOrderError):
        client.submit_signal(idempotency_key=uuid4(), market="KR", symbol="005930")

    assert not requests
    client.close()


def test_real_signal_sends_only_market_and_symbol_to_local_spring(monkeypatch) -> None:
    enable_real_signal_gates(monkeypatch)
    key = uuid4()
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202, json={"status": "ACCEPTED"})

    client = SpringRealSignalOrderClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    response = client.submit_signal(idempotency_key=key, market="KR", symbol=" 005930 ")

    request = captured[0]
    assert response["status"] == "ACCEPTED"
    assert request.url.path == "/api/v1/kis/orders/real/signals"
    assert request.headers["Idempotency-Key"] == str(key)
    assert request.read() == b'{"market":"KR","symbol":"005930"}'
    client.close()


def test_real_signal_us_requires_exchange_before_http(monkeypatch) -> None:
    enable_real_signal_gates(monkeypatch)
    requests = []
    client = SpringRealSignalOrderClient(
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request)))
    )

    with pytest.raises(ValueError):
        client.submit_signal(idempotency_key=uuid4(), market="US", symbol="AAPL")

    assert not requests
    client.close()


def test_unknown_transport_outcome_is_never_retried(monkeypatch) -> None:
    enable_real_signal_gates(monkeypatch)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out")

    client = SpringRealSignalOrderClient(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RealSignalOrderOutcomeUnknown):
        client.submit_signal(idempotency_key=uuid4(), market="US", symbol="AAPL", exchange="NASD")

    assert calls == 1
    client.close()


def test_local_only_endpoint_is_required() -> None:
    with pytest.raises(ValueError):
        SpringRealSignalOrderClient("http://example.com")


def test_all_real_gates_must_be_enabled(monkeypatch) -> None:
    enable_real_signal_gates(monkeypatch)
    assert real_signal_submission_enabled()
    monkeypatch.setenv("KIS_REAL_ORDERS_ENABLED", "false")
    assert not real_signal_submission_enabled()


def test_real_entries_stay_locked_until_protective_exit_manager_is_enabled(monkeypatch) -> None:
    enable_real_signal_gates(monkeypatch)
    monkeypatch.setenv("REAL_PROTECTIVE_EXIT_MANAGER_ENABLED", "false")
    assert not real_signal_submission_enabled()


@pytest.mark.parametrize("gate", [
    "KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED",
    "KIS_REAL_RECONCILIATION_ENABLED",
])
def test_real_entries_stay_locked_without_protective_automation_and_reconciliation(monkeypatch, gate: str) -> None:
    enable_real_signal_gates(monkeypatch)
    monkeypatch.setenv(gate, "false")
    assert not real_signal_submission_enabled()
