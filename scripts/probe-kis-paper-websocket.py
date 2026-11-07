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
    "KIS_WEBSOCKET_ENABLED": "true",
    "KIS_PAPER_ORDERS_ENABLED": "false",
    "KIS_REAL_ORDERS_ENABLED": "false",
    "KIS_REAL_RECONCILIATION_ENABLED": "false",
    "KIS_REAL_ACCOUNT_READ_ENABLED": "false",
    "KIS_REAL_EXECUTION_NOTICES_ENABLED": "false",
    "KIS_REAL_MARKET_DATA_ENABLED": "false",
    "KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED": "false",
    "REAL_SIGNAL_MONITOR_ENABLED": "false",
    "REAL_SIGNAL_SUBMISSION_ENABLED": "false",
    "REAL_PROTECTIVE_EXIT_MANAGER_ENABLED": "false",
    "PAPER_AUTOTRADING_ENABLED": "false",
    "KIS_SHARED_RATE_LIMIT_ENABLED": "true",
}


async def run_probe(
    settings: KisSettings,
    stream: StreamSettings,
    market: str,
    symbol: str,
    exchange: str | None,
    wait_for_tick_seconds: int,
) -> dict[str, Any]:
    key = await asyncio.to_thread(approval_key, settings)
    expected_transaction_id = "H0STCNT0" if market == "KR" else "HDFSCNT0"
    message = subscription_message(key, market, symbol, exchange)
    result: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "environment": "paper",
        "market": market,
        "symbol": symbol.upper(),
        "exchange": exchange,
        "acknowledged": False,
        "tick_received": False,
        "unsubscribe_sent": False,
        "order_sent": False,
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
                unsubscribe = subscription_message(key, market, symbol, exchange, tr_type="2")
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


def main(
    market: str = "KR",
    symbol: str = "005930",
    exchange: str | None = None,
    wait_for_tick_seconds: int = 0,
) -> int:
    try:
        source = load_local_environment(repository)
    except (OSError, ValueError):
        print(json.dumps({"status": "blocked", "reason": "LOCAL_ENV_UNAVAILABLE"}))
        return 2
    for name in ("KIS_REAL_APP_KEY", "KIS_REAL_APP_SECRET", "KIS_REAL_ACCOUNT_NUMBER"):
        os.environ[name] = ""

    if any(os.getenv(name, "").strip().lower() != expected for name, expected in _REQUIRED_SWITCHES.items()):
        print(json.dumps({"status": "blocked", "reason": "SAFETY_SWITCHES_NOT_PAPER_READ_ONLY"}))
        return 2
    if market != "KR" or exchange is not None:
        print(json.dumps({"status": "blocked", "reason": "PAPER_PROBE_SUPPORTS_DOMESTIC_ONLY"}))
        return 2
    if not 0 <= wait_for_tick_seconds <= 3600:
        print(json.dumps({"status": "blocked", "reason": "INVALID_PROBE_ARGUMENT"}))
        return 2

    settings = KisSettings.from_environment("paper")
    stream = StreamSettings.from_environment("paper")
    if not stream.enabled or not settings.app_key or not settings.app_secret or not settings.shared_rate_limit_enabled:
        print(json.dumps({"status": "blocked", "reason": "PAPER_WEBSOCKET_CREDENTIALS_UNAVAILABLE"}))
        return 2
    try:
        # Validate symbol and exchange before requesting an approval key.
        subscription_message("probe", market, symbol, exchange)
    except ValueError:
        print(json.dumps({"status": "blocked", "reason": "INVALID_SUBSCRIPTION"}))
        return 2

    result = asyncio.run(run_probe(settings, stream, market, symbol, exchange, wait_for_tick_seconds))
    result["local_env_file"] = source.name
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] == "tick_received":
        return 0
    if result["status"] == "acknowledged_no_tick" and wait_for_tick_seconds == 0:
        return 0
    return 3


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PAPER-only read-only KIS WebSocket probe.")
    parser.add_argument("--market", choices=("KR",), default="KR")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--wait-for-tick-seconds", type=int, default=0, metavar="SECONDS",
                        help="Wait for an actual tick; 0 checks subscription acknowledgement only.")
    args = parser.parse_args()
    symbol = args.symbol or "005930"
    raise SystemExit(main(args.market, symbol, None, args.wait_for_tick_seconds))
