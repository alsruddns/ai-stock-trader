from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from app.paper_signal_orders import (
    PaperSignalOrderError,
    PaperSignalOrderOutcomeUnknown,
    SpringKisPaperSignalOrderClient,
    paper_signal_idempotency_key,
    paper_signal_submission_enabled,
)


def enable_paper_signal_gates(monkeypatch) -> None:
    for key, value in {
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
    }.items():
        monkeypatch.setenv(key, value)


def test_client_uses_local_spring_port() -> None:
    client = SpringKisPaperSignalOrderClient()
    assert client.base_url == "http://127.0.0.1:18080"
    client.close()


def test_submission_is_locked_by_default_and_sends_no_request(monkeypatch) -> None:
    requests = []
    client = SpringKisPaperSignalOrderClient(
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request)))
    )

    with pytest.raises(PaperSignalOrderError):
        client.submit_signal(idempotency_key=uuid4(), market="KR", symbol="005930")

    assert requests == []
    client.close()


def test_signal_sends_only_market_and_symbol_to_spring(monkeypatch) -> None:
    enable_paper_signal_gates(monkeypatch)
    key = uuid4()
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202, json={"status": "ACCEPTED"})

    client = SpringKisPaperSignalOrderClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = client.submit_signal(idempotency_key=key, market="KR", symbol=" 005930 ")

    assert result == {"status": "ACCEPTED"}
    assert captured[0].url.path == "/api/v1/kis/orders/paper/signals"
    assert captured[0].headers["Idempotency-Key"] == str(key)
    assert captured[0].read() == b'{"market":"KR","symbol":"005930"}'
    client.close()


@pytest.mark.parametrize("market,symbol,exchange", [("US", "AAPL", "NASD"), ("KR", "005930", "NASD")])
def test_current_domestic_scope_rejects_non_domestic_signal_before_http(monkeypatch, market, symbol, exchange) -> None:
    enable_paper_signal_gates(monkeypatch)
    requests = []
    client = SpringKisPaperSignalOrderClient(
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request)))
    )

    with pytest.raises(ValueError):
        client.submit_signal(idempotency_key=uuid4(), market=market, symbol=symbol, exchange=exchange)

    assert requests == []
    client.close()


def test_transport_failure_is_unknown_and_never_retried(monkeypatch) -> None:
    enable_paper_signal_gates(monkeypatch)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out")

    client = SpringKisPaperSignalOrderClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(PaperSignalOrderOutcomeUnknown):
        client.submit_signal(idempotency_key=uuid4(), market="KR", symbol="005930")

    assert calls == 1
    client.close()


def test_server_5xx_is_unknown_and_never_retried(monkeypatch) -> None:
    enable_paper_signal_gates(monkeypatch)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"detail": "internal error"})

    client = SpringKisPaperSignalOrderClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(PaperSignalOrderOutcomeUnknown):
        client.submit_signal(idempotency_key=uuid4(), market="KR", symbol="005930")

    assert calls == 1
    client.close()


def test_all_paper_signal_gates_are_required(monkeypatch) -> None:
    enable_paper_signal_gates(monkeypatch)
    assert paper_signal_submission_enabled()
    monkeypatch.setenv("KIS_PAPER_CANCEL_PREFLIGHT_VERIFIED", "false")
    assert not paper_signal_submission_enabled()


def test_bar_idempotency_key_is_stable_across_timezone_and_model_changes() -> None:
    timestamp = datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc)
    local = timestamp.astimezone(timezone(timedelta(hours=9)))
    first = paper_signal_idempotency_key(
        market="KR", symbol="005930", exchange=None, bar_timestamp=timestamp,
    )
    after_restart = paper_signal_idempotency_key(
        market="KR", symbol="005930", exchange=None, bar_timestamp=local,
    )
    assert first == after_restart


def test_bar_idempotency_key_requires_aware_timestamp_and_market_exchange_pair() -> None:
    with pytest.raises(ValueError):
        paper_signal_idempotency_key(
            market="KR", symbol="005930", exchange=None,
            bar_timestamp=datetime(2026, 11, 16, 9, 0),
        )
    with pytest.raises(ValueError):
        paper_signal_idempotency_key(
            market="US", symbol="AAPL", exchange=None,
            bar_timestamp=datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc),
        )
