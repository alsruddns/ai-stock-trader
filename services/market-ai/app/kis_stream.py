"""Read-only KIS WebSocket market data helpers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Literal

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
    def from_environment(cls) -> "StreamSettings":
        return cls(
            enabled=os.getenv("KIS_WEBSOCKET_ENABLED", "false").strip().lower() == "true",
            kis=KisSettings.from_environment(),
        )

    @property
    def websocket_url(self) -> str:
        port = "31000" if self.kis.environment == "paper" else "21000"
        return f"{_WS_HOST}{port}"


def approval_key(settings: KisSettings, client: httpx.Client | None = None) -> str:
    """Request an environment-specific KIS WebSocket approval key."""
    if not settings.app_key or not settings.app_secret:
        raise ValueError("KIS app key and secret are required")
    owned_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0))
    try:
        if settings.shared_rate_limit_enabled:
            interval = 1.0 if settings.environment == "paper" else 0.1
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


def subscription_message(key: str, market: Market, symbol: str, exchange: str | None = None) -> dict:
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
        "header": {"approval_key": key, "custtype": "P", "tr_type": "1", "content-type": "utf-8"},
        "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
    }


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

