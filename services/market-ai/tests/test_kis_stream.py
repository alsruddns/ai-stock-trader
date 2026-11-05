from __future__ import annotations

from datetime import timezone

import httpx
import pytest

from app.kis import KisApiError, KisSettings
from app.kis_stream import StreamSettings, approval_key, normalize_stream_frame, subscription_message


def test_approval_key_uses_environment_specific_url_and_official_payload() -> None:
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        return httpx.Response(200, json={"approval_key": "ws-key"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    key = approval_key(KisSettings(True, "paper", "paper-key", "paper-secret"), client)
    assert key == "ws-key"
    request = requests_seen[0]
    assert request.url.path == "/oauth2/Approval"
    assert request.read().decode() == '{"grant_type":"client_credentials","appkey":"paper-key","secretkey":"paper-secret"}'
    client.close()


def test_approval_key_rejects_invalid_response_without_returning_raw_body() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"secret": "do-not-expose"})))
    with pytest.raises(KisApiError, match="missing required fields") as exc:
        approval_key(KisSettings(True, "real", "key", "secret"), client)
    assert "do-not-expose" not in str(exc.value)
    client.close()


def test_subscription_message_uses_official_domestic_and_overseas_tr_ids() -> None:
    kr = subscription_message("approval", "KR", "005930")
    us = subscription_message("approval", "US", "aapl", "NAS")
    assert kr["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "005930"}
    assert us["body"]["input"] == {"tr_id": "HDFSCNT0", "tr_key": "DNASAAPL"}
    assert kr["header"]["tr_type"] == "1"
    assert us["header"]["custtype"] == "P"


@pytest.mark.parametrize(
    ("market", "symbol", "exchange"),
    [("KR", "A005930", None), ("US", "AAPL", None), ("US", "AAPL", "LSE"), ("US", "!", "NAS")],
)
def test_invalid_subscription_is_rejected(market: str, symbol: str, exchange: str | None) -> None:
    with pytest.raises(ValueError):
        subscription_message("key", market, symbol, exchange)  # type: ignore[arg-type]


def test_frame_normalizer_bounds_and_redacts_control_payload() -> None:
    data = normalize_stream_frame("0|H0STCNT0|001|005930^09:00:00^73500")
    control = normalize_stream_frame('{"header":{"tr_id":"PINGPONG"},"body":{"output":{"msg1":"ok","approval_key":"secret"}}}')
    assert data["transaction_id"] == "H0STCNT0"
    assert data["payload"].startswith("005930^")
    assert control == {"kind": "control", "code": "PINGPONG", "message": "ok"}
    with pytest.raises(KisApiError, match="size"):
        normalize_stream_frame("x" * 100_001)


def test_stream_url_selects_distinct_paper_and_real_ports() -> None:
    paper = StreamSettings(True, KisSettings(True, "paper", "k", "s"))
    real = StreamSettings(True, KisSettings(True, "real", "k", "s"))
    assert paper.websocket_url == "ws://ops.koreainvestment.com:31000"
    assert real.websocket_url == "ws://ops.koreainvestment.com:21000"
