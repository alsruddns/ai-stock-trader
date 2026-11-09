from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))
sys.path.insert(0, str(repository / "scripts" / "lib"))

import httpx
import websockets
from app.kis import KisSettings
from app.kis_stream import (
    StreamSettings,
    approval_key,
    normalize_stream_frame,
    reply_to_pingpong,
    subscription_message,
)
from kis_local_env import load_local_environment


_REQUIRED_SWITCHES = {
    "TRADING_MODE": "paper",
    "LIVE_TRADING_ENABLED": "false",
    "KIS_ENVIRONMENT": "paper",
    "KIS_MARKET_DATA_ENABLED": "true",
    "KIS_WEBSOCKET_ENABLED": "true",
    "KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED": "true",
    "KIS_PAPER_ORDERS_ENABLED": "false",
    "KIS_REAL_ORDERS_ENABLED": "false",
    "PAPER_AUTOTRADING_ENABLED": "false",
    "KIS_REAL_ACCOUNT_READ_ENABLED": "false",
    "KIS_REAL_RECONCILIATION_ENABLED": "false",
}
_FORBIDDEN_SWITCHES = (
    "KIS_REAL_EXECUTION_NOTICES_ENABLED",
    "REAL_SIGNAL_MONITOR_ENABLED",
    "REAL_SIGNAL_SUBMISSION_ENABLED",
    "REAL_PROTECTIVE_EXIT_MANAGER_ENABLED",
    "KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED",
)


def local_real_feed_is_active() -> bool | None:
    """Avoid opening another KIS REAL socket while the managed feed owns one."""
    try:
        response = httpx.get("http://127.0.0.1:18000/api/v1/paper/session", timeout=1.5)
    except httpx.ConnectError:
        return False
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        status = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(status, dict) or not isinstance(status.get("stream_connections"), dict):
        return None
    active_states = {"CONNECTING", "SUBSCRIBING", "RUNNING", "RECONNECTING"}
    return bool(status.get("enabled") and status["stream_connections"].get("real") in active_states)


def isolate_read_only_real_market_data_process() -> None:
    """Keep only REAL quote credentials; remove account and every order feature."""
    for name in tuple(os.environ):
        if name.startswith(("KIS_PAPER_", "REAL_SIGNAL_", "REAL_PROTECTIVE_")):
            os.environ.pop(name, None)
    os.environ.pop("KIS_REAL_ACCOUNT_NUMBER", None)
    for name in (
        "KIS_ACCOUNT_READ_ENABLED",
        "KIS_PAPER_ACCOUNT_READ_ENABLED",
        "KIS_REAL_ACCOUNT_READ_ENABLED",
        "KIS_REAL_RECONCILIATION_ENABLED",
        "KIS_REAL_EXECUTION_NOTICES_ENABLED",
        "KIS_REAL_MARKET_DATA_ENABLED",
        "KIS_PAPER_ORDERS_ENABLED",
        "KIS_REAL_ORDERS_ENABLED",
        "PAPER_AUTOTRADING_ENABLED",
        "REAL_SIGNAL_MONITOR_ENABLED",
        "REAL_SIGNAL_SUBMISSION_ENABLED",
        "REAL_PROTECTIVE_EXIT_MANAGER_ENABLED",
        "KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED",
    ):
        os.environ[name] = "false"
    os.environ["TRADING_MODE"] = "paper"
    os.environ["KIS_ENVIRONMENT"] = "paper"
    os.environ["KIS_SHARED_RATE_LIMIT_ENABLED"] = "true"


async def run_probe(
    settings: KisSettings,
    stream: StreamSettings,
    symbol: str,
    wait_for_tick_seconds: int,
) -> dict[str, Any]:
    key = await asyncio.to_thread(approval_key, settings)
    expected_transaction_id = "H0STCNT0"
    message = subscription_message(key, "KR", symbol)
    result: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "environment": "real_market_data_read_only",
        "market": "KR",
        "symbol": symbol,
        "acknowledged": False,
        "tick_received": False,
        "unsubscribe_sent": False,
        "order_sent": False,
        "account_read_sent": False,
    }
    timeout_seconds = wait_for_tick_seconds if wait_for_tick_seconds > 0 else 15
    try:
        async with websockets.connect(
            stream.websocket_url,
            ping_interval=30,
            close_timeout=3,
            open_timeout=10,
        ) as upstream:
            await upstream.send(json.dumps(message, separators=(",", ":")))
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                remaining = deadline - asyncio.get_running_loop().time()
                try:
                    raw = await asyncio.wait_for(upstream.recv(), timeout=min(3.0, remaining))
                except asyncio.TimeoutError:
                    continue
                frame = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                if await reply_to_pingpong(upstream, raw):
                    continue
                normalized = normalize_stream_frame(frame)
                if (normalized.get("kind") == "data"
                        and normalized.get("transaction_id") == expected_transaction_id):
                    result["tick_received"] = True
                    break
                try:
                    payload = json.loads(frame)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
                body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
                response_code = body.get("rt_cd")
                if header.get("tr_id") != expected_transaction_id or response_code is None:
                    continue
                if str(response_code) == "0":
                    result["acknowledged"] = True
                    if wait_for_tick_seconds == 0:
                        break
                    continue
                broker_code = body.get("msg_cd")
                result["status"] = "rejected"
                result["broker_code"] = (
                    broker_code if isinstance(broker_code, str)
                    and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", broker_code) else None
                )
                break

            if result["acknowledged"] or result["tick_received"]:
                unsubscribe = subscription_message(key, "KR", symbol, tr_type="0")
                await upstream.send(json.dumps(unsubscribe, separators=(",", ":")))
                result["unsubscribe_sent"] = True
    except Exception as exc:
        result["status"] = "request_failed"
        result["failure_type"] = type(exc).__name__
        return result

    if result.get("status") == "rejected":
        return result
    if result["tick_received"]:
        result["status"] = "tick_received"
    elif result["acknowledged"]:
        result["status"] = "acknowledged_no_tick"
    else:
        result["status"] = "timeout"
    return result


def main(symbol: str = "005930", wait_for_tick_seconds: int = 0) -> int:
    try:
        source = load_local_environment(repository)
    except (OSError, ValueError):
        print(json.dumps({"status": "blocked", "reason": "LOCAL_ENV_UNAVAILABLE"}))
        return 2
    if (any(os.getenv(name, "").strip().lower() != expected for name, expected in _REQUIRED_SWITCHES.items())
            or any(os.getenv(name, "false").strip().lower() == "true" for name in _FORBIDDEN_SWITCHES)):
        print(json.dumps({"status": "blocked", "reason": "READ_ONLY_REAL_KR_STREAM_SWITCHES_NOT_SAFE"}))
        return 2
    if not 0 <= wait_for_tick_seconds <= 3600:
        print(json.dumps({"status": "blocked", "reason": "INVALID_PROBE_ARGUMENT"}))
        return 2

    try:
        symbol = symbol.strip().upper()
        subscription_message("probe", "KR", symbol)
    except ValueError:
        print(json.dumps({"status": "blocked", "reason": "INVALID_SUBSCRIPTION"}))
        return 2

    local_feed_active = local_real_feed_is_active()
    if local_feed_active is None:
        print(json.dumps({"status": "blocked", "reason": "LOCAL_REAL_STREAM_STATUS_UNAVAILABLE"}))
        return 2
    if local_feed_active:
        print(json.dumps({"status": "blocked", "reason": "LOCAL_REAL_STREAM_ALREADY_ACTIVE"}))
        return 2

    isolate_read_only_real_market_data_process()
    settings = KisSettings.from_environment("real")
    stream = StreamSettings.from_environment("real")
    if (not stream.enabled or not settings.enabled or not settings.app_key or not settings.app_secret
            or not settings.shared_rate_limit_enabled):
        print(json.dumps({"status": "blocked", "reason": "REAL_KR_WEBSOCKET_CREDENTIALS_UNAVAILABLE"}))
        return 2

    result = asyncio.run(run_probe(settings, stream, symbol, wait_for_tick_seconds))
    result["local_env_file"] = source.name
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] == "tick_received":
        return 0
    if result["status"] == "acknowledged_no_tick" and wait_for_tick_seconds == 0:
        return 0
    return 3


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only KIS REAL KR WebSocket subscription probe; never sends orders or reads accounts.")
    parser.add_argument("--symbol", default="005930", help="Six-digit domestic stock code")
    parser.add_argument("--wait-for-tick-seconds", type=int, default=0, metavar="SECONDS",
                        help="Wait for an actual quote tick; 0 checks subscription acknowledgement only.")
    args = parser.parse_args()
    raise SystemExit(main(args.symbol, args.wait_for_tick_seconds))
