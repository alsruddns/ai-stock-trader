from __future__ import annotations

import asyncio
from datetime import timezone

import httpx
import pytest

from app.kis import KisApiError, KisSettings
from app.kis_rate_limit import SharedRateLimitUnavailable
from app.kis_stream import (
    StreamSettings, SubscriptionAckTracker, SubscriptionAcknowledgementError,
    approval_key, is_pingpong_frame, normalize_stream_frame, parse_subscription_ack,
    reply_to_pingpong, subscription_message, subscription_messages,
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


def test_domestic_watch_subscribes_to_trade_and_orderbook_but_us_stays_single_stream() -> None:
    kr = subscription_messages("approval", "KR", "005930")
    us = subscription_messages("approval", "US", "AAPL", "NAS")
    assert [message["body"]["input"]["tr_id"] for message in kr] == ["H0STCNT0", "H0STASP0"]
    assert [message["body"]["input"]["tr_key"] for message in kr] == ["005930", "005930"]
    assert [message["body"]["input"]["tr_id"] for message in us] == ["HDFSCNT0"]
    assert all(message["header"]["tr_type"] == "1" for message in kr + us)


def test_subscription_message_supports_official_unsubscribe_type() -> None:
    message = subscription_message("approval", "KR", "005930", tr_type="0")
    assert message["header"]["tr_type"] == "0"
    assert message["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "005930"}
    assert [item["header"]["tr_type"] for item in subscription_messages(
        "approval", "KR", "005930", tr_type="0",
    )] == ["0", "0"]


def test_subscription_message_rejects_unsupported_subscription_type() -> None:
    with pytest.raises(ValueError, match="subscription type"):
        subscription_message("approval", "KR", "005930", tr_type="2")


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


def test_subscription_ack_tracker_requires_every_requested_subscription() -> None:
    tracker = SubscriptionAckTracker({"H0STCNT0": 1, "HDFSCNT0": 2})
    assert tracker.complete is False
    assert tracker.observe('{"header":{"tr_id":"HDFSCNT0"},"body":{"rt_cd":"0"}}') is False
    assert tracker.status == {"accepted_count": 1, "required_count": 3, "complete": False}
    assert tracker.observe('{"header":{"tr_id":"OTHER"},"body":{"rt_cd":"0"}}') is False
    assert tracker.observe('{"header":{"tr_id":"HDFSCNT0"},"body":{"rt_cd":"0"}}') is False
    assert tracker.observe('{"header":{"tr_id":"H0STCNT0"},"body":{"rt_cd":"0"}}') is True
    assert tracker.complete is True


def test_subscription_ack_tracker_caps_duplicate_acknowledgements() -> None:
    tracker = SubscriptionAckTracker({"H0STCNT0": 1})
    positive_ack = '{"header":{"tr_id":"H0STCNT0"},"body":{"rt_cd":"0"}}'
    assert tracker.observe(positive_ack) is True
    tracker.observe(positive_ack)
    assert tracker.status == {"accepted_count": 1, "required_count": 1, "complete": True}


def test_subscription_ack_tracker_can_require_more_subscriptions_after_initial_connection() -> None:
    tracker = SubscriptionAckTracker({"H0STCNT0": 1})
    assert tracker.observe('{"header":{"tr_id":"H0STCNT0"},"body":{"rt_cd":"0"}}') is True

    tracker.require("H0STASP0")

    assert tracker.complete is False
    assert tracker.status == {"accepted_count": 1, "required_count": 2, "complete": False}
    assert tracker.observe('{"header":{"tr_id":"H0STASP0"},"body":{"rt_cd":"0"}}') is True


def test_subscription_ack_tracker_reports_sanitized_rejection_code_only() -> None:
    tracker = SubscriptionAckTracker({"HDFSCNT0": 1})
    rejection = ('{"header":{"tr_id":"HDFSCNT0"},"body":{"rt_cd":"1",'
                 '"msg_cd":"SAFE_CODE","msg1":"private details"}}')
    with pytest.raises(SubscriptionAcknowledgementError) as exc:
        tracker.observe(rejection)
    assert exc.value.broker_code == "SAFE_CODE"
    assert "private details" not in str(exc.value)


def test_subscription_ack_parser_rejects_untrusted_codes_and_oversized_frames() -> None:
    malformed_code = '{"header":{"tr_id":"HDFSCNT0"},"body":{"rt_cd":"0","msg_cd":"private token!"}}'
    ack = parse_subscription_ack(malformed_code)
    assert ack == {"transaction_id": "HDFSCNT0", "accepted": True, "broker_code": "UNSPECIFIED"}
    assert parse_subscription_ack("x" * 100_001) is None
