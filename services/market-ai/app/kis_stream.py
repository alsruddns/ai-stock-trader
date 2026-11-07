"""Read-only KIS WebSocket market data helpers."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from .kis import KisApiError, KisSettings, KisSharedRateLimitUnavailable
from .kis_rate_limit import SharedRateLimitUnavailable, wait_for_kis_rest_slot

Market = Literal["KR", "US"]
_KR_SYMBOL = re.compile(r"^\d{6}$")
_US_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
_EXCHANGE_CODE = {"NAS": "DNAS", "NYS": "DNYS", "AMS": "DAMS"}
_WS_HOST = "ws://ops.koreainvestment.com:"


@dataclass(frozen=True)
class StreamSettings:
    enabled: bool
    kis: KisSettings

    @classmethod
    def from_environment(cls, environment: Literal["paper", "real"] | None = None) -> "StreamSettings":
        return cls(
            enabled=os.getenv("KIS_WEBSOCKET_ENABLED", "false").strip().lower() == "true",
            kis=KisSettings.from_environment(environment),
        )

    @property
    def websocket_url(self) -> str:
        port = "31000" if self.kis.environment == "paper" else "21000"
        return f"{_WS_HOST}{port}"


class SubscriptionAcknowledgementError(RuntimeError):
    """A KIS WebSocket subscription was explicitly rejected."""

    def __init__(self, broker_code: str | None) -> None:
        self.broker_code = broker_code
        super().__init__("KIS WebSocket subscription was rejected")


class SubscriptionAcknowledgementTimeoutError(RuntimeError):
    """KIS did not acknowledge the requested market-data subscriptions in time."""


class SubscriptionAckTracker:
    """Require one positive KIS acknowledgement for every requested transaction id."""

    def __init__(self, required_by_transaction_id: dict[str, int]) -> None:
        if (not required_by_transaction_id
                or any(not re.fullmatch(r"[A-Z0-9]{1,32}", key) or count < 1
                       for key, count in required_by_transaction_id.items())):
            raise ValueError("subscription acknowledgement requirements are invalid")
        self._required = dict(required_by_transaction_id)
        self._accepted: Counter[str] = Counter()

    @property
    def complete(self) -> bool:
        return all(self._accepted[key] >= count for key, count in self._required.items())

    @property
    def status(self) -> dict[str, int | bool]:
        return {
            "accepted_count": sum(min(self._accepted[key], count) for key, count in self._required.items()),
            "required_count": sum(self._required.values()),
            "complete": self.complete,
        }

    def observe(self, frame: str | bytes) -> bool:
        acknowledgement = parse_subscription_ack(frame)
        if acknowledgement is None:
            return self.complete
        transaction_id = acknowledgement["transaction_id"]
        if transaction_id not in self._required:
            return self.complete
        if not acknowledgement["accepted"]:
            raise SubscriptionAcknowledgementError(acknowledgement["broker_code"])
        if self._accepted[transaction_id] < self._required[transaction_id]:
            self._accepted[transaction_id] += 1
        return self.complete


def parse_subscription_ack(frame: str | bytes) -> dict[str, str | bool] | None:
    """Parse only bounded KIS subscribe responses; do not retain or emit raw messages."""
    if isinstance(frame, bytes):
        frame = frame.decode("utf-8", errors="replace")
    if len(frame) > 100_000:
        return None
    try:
        message = json.loads(frame)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(message, dict):
        return None
    header = message.get("header") if isinstance(message.get("header"), dict) else {}
    body = message.get("body") if isinstance(message.get("body"), dict) else {}
    transaction_id = header.get("tr_id")
    response_code = body.get("rt_cd")
    if not isinstance(transaction_id, str) or not re.fullmatch(r"[A-Z0-9]{1,32}", transaction_id):
        return None
    if str(response_code) not in {"0", "1"}:
        return None
    broker_code = body.get("msg_cd")
    return {
        "transaction_id": transaction_id,
        "accepted": str(response_code) == "0",
        "broker_code": (
            broker_code if isinstance(broker_code, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", broker_code) else "UNSPECIFIED"
        ),
    }


def approval_key(settings: KisSettings, client: httpx.Client | None = None) -> str:
    """Request an environment-specific KIS WebSocket approval key."""
    if not settings.app_key or not settings.app_secret:
        raise ValueError("KIS app key and secret are required")
    owned_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0))
    try:
        if settings.shared_rate_limit_enabled:
            interval = 1.0
            try:
                wait_for_kis_rest_slot(settings.environment, settings.app_key, interval)
            except SharedRateLimitUnavailable as exc:
                raise KisSharedRateLimitUnavailable("shared KIS request pacing is unavailable") from exc
        response = http.post(
            f"{settings.base_url}/oauth2/Approval",
            json={"grant_type": "client_credentials", "appkey": settings.app_key, "secretkey": settings.app_secret},
            headers={"content-type": "application/json"},
        )
        response.raise_for_status()
        key = response.json().get("approval_key")
        if not isinstance(key, str) or not key:
            raise KisApiError("KIS WebSocket approval response was missing required fields")
        return key
    except KisApiError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise KisApiError("KIS WebSocket authentication failed; check credentials and environment") from exc
    finally:
        if owned_client:
            http.close()


def subscription_message(
    key: str, market: Market, symbol: str, exchange: str | None = None, *, tr_type: str = "1",
) -> dict:
    if tr_type not in {"1", "2"}:
        raise ValueError("subscription type must be 1 (subscribe) or 2 (unsubscribe)")
    normalized = symbol.strip().upper()
    if market == "KR":
        if not _KR_SYMBOL.fullmatch(normalized) or exchange is not None:
            raise ValueError("KR stream subscriptions require a six-digit symbol and no exchange")
        tr_id, tr_key = "H0STCNT0", normalized
    elif market == "US":
        if exchange not in _EXCHANGE_CODE or not _US_SYMBOL.fullmatch(normalized):
            raise ValueError("US stream subscriptions require a valid symbol and exchange NAS, NYS, or AMS")
        tr_id, tr_key = "HDFSCNT0", f"{_EXCHANGE_CODE[exchange]}{normalized}"
    else:
        raise ValueError("market must be KR or US")
    return {
        "header": {"approval_key": key, "custtype": "P", "tr_type": tr_type, "content-type": "utf-8"},
        "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
    }


def is_pingpong_frame(frame: str | bytes) -> bool:
    """Return whether a KIS control frame requests a WebSocket Pong response."""
    if isinstance(frame, bytes):
        frame = frame.decode("utf-8", errors="replace")
    try:
        message = json.loads(frame)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(message, dict):
        return False
    header = message.get("header")
    return isinstance(header, dict) and header.get("tr_id") == "PINGPONG"


async def reply_to_pingpong(upstream: Any, frame: str | bytes) -> bool:
    """Echo KIS PINGPONG control data in a WebSocket Pong frame."""
    if not is_pingpong_frame(frame):
        return False
    payload = frame if isinstance(frame, bytes) else frame.encode("utf-8")
    await upstream.pong(payload)
    return True


def normalize_stream_frame(frame: str) -> dict[str, str]:
    """Expose only a bounded, credential-free frame representation to local consumers."""
    if len(frame) > 100_000:
        raise KisApiError("KIS WebSocket frame exceeded the allowed size")
    parts = frame.split("|", 3)
    if len(parts) == 4 and parts[0].isdigit():
        return {"kind": "data", "encrypted": parts[0], "transaction_id": parts[1][:32], "count": parts[2][:8], "payload": parts[3]}
    try:
        message = json.loads(frame)
    except (json.JSONDecodeError, TypeError):
        return {"kind": "control", "message": frame[:2048]}
    if isinstance(message, dict):
        header = message.get("header") if isinstance(message.get("header"), dict) else {}
        body = message.get("body") if isinstance(message.get("body"), dict) else {}
        output = body.get("output") if isinstance(body.get("output"), dict) else {}
        return {
            "kind": "control",
            "code": str(header.get("tr_id", ""))[:32],
            "message": str(output.get("msg1", body.get("msg1", "")))[:256],
        }
    return {"kind": "control", "message": "KIS sent an unsupported control frame"}

