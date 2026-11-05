from __future__ import annotations

import asyncio
from datetime import timezone

import httpx
import pytest

from app.kis import KisApiError, KisSettings
from app.kis_rate_limit import SharedRateLimitUnavailable
from app.kis_stream import (
    StreamSettings, approval_key, is_pingpong_frame, normalize_stream_frame,
    reply_to_pingpong, subscription_message,
)


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


def test_approval_key_fails_closed_when_shared_rate_limit_is_unavailable(monkeypatch) -> None:
    settings = KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=True)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda _request: pytest.fail("KIS must not be called without a shared slot")
    ))
    monkeypatch.setattr("app.kis_stream.wait_for_kis_rest_slot",
                        lambda *_args: (_ for _ in ()).throw(SharedRateLimitUnavailable("database unavailable")))
    with pytest.raises(KisApiError, match="shared KIS request pacing is unavailable"):
        approval_key(settings, client)
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


def test_kis_pingpong_control_frame_is_answered_as_websocket_pong() -> None:
    class FakeUpstream:
        def __init__(self) -> None:
            self.pong_payload: bytes | None = None

        async def pong(self, payload: bytes) -> None:
            self.pong_payload = payload

    frame = '{"header":{"tr_id":"PINGPONG"},"body":{"rt_cd":"0"}}'
    upstream = FakeUpstream()

    assert is_pingpong_frame(frame)
    assert asyncio.run(reply_to_pingpong(upstream, frame)) is True
    assert upstream.pong_payload == frame.encode("utf-8")


def test_non_pingpong_control_frame_does_not_send_pong() -> None:
    class FakeUpstream:
        async def pong(self, _payload: bytes) -> None:
            pytest.fail("subscription replies must not be treated as keepalives")

    frame = '{"header":{"tr_id":"H0STCNT0"},"body":{"rt_cd":"0"}}'
    assert not is_pingpong_frame(frame)
    assert asyncio.run(reply_to_pingpong(FakeUpstream(), frame)) is False


def test_stream_url_selects_distinct_paper_and_real_ports() -> None:
    paper = StreamSettings(True, KisSettings(True, "paper", "k", "s"))
    real = StreamSettings(True, KisSettings(True, "real", "k", "s"))
    assert paper.websocket_url == "ws://ops.koreainvestment.com:31000"
    assert real.websocket_url == "ws://ops.koreainvestment.com:21000"
