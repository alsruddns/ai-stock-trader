"""Opt-in continuous paper-session market-data monitor.

This component only collects KIS paper-market ticks and closes minute bars. It has
no order-writing dependency; paper order automation remains a separate gated phase.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections import deque
from dataclasses import dataclass
from typing import Literal

from .kis_stream import StreamSettings, approval_key, normalize_stream_frame, subscription_message
from .market_stream import MinuteBarAggregator, parse_trade_frame
from .quant import MinuteBar


@dataclass(frozen=True)
class PaperWatch:
    market: Literal["KR", "US"]
    symbol: str
    exchange: str | None = None

    @property
    def key(self) -> str:
        return f"{self.market}:{self.symbol}:{self.exchange or ''}"


def parse_watchlist(value: str) -> tuple[PaperWatch, ...]:
    """Parse semicolon-separated KR:005930 or US:AAPL:NAS subscriptions."""
    watches: list[PaperWatch] = []
    seen: set[str] = set()
    for item in filter(None, (part.strip() for part in value.split(";"))):
        fields = [part.strip().upper() for part in item.split(":")]
        if len(fields) == 2 and fields[0] == "KR":
            subscription_message("pending", "KR", fields[1])
            watch = PaperWatch("KR", fields[1])
        elif len(fields) == 3 and fields[0] == "US":
            subscription_message("pending", "US", fields[1], fields[2])
            watch = PaperWatch("US", fields[1], fields[2])
        else:
            raise ValueError("watchlist entries must be KR:000000 or US:SYMBOL:NAS|NYS|AMS")
        if watch.key not in seen:
            watches.append(watch)
            seen.add(watch.key)
    if len(watches) > 50:
        raise ValueError("paper monitor supports at most 50 subscriptions")
    return tuple(watches)


class PaperSessionFeedMonitor:
    """Reconnectable paper WebSocket collector with bounded in-memory bar history."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._task: asyncio.Task | None = None
        self._status: dict[str, object] = {
            "state": "DISABLED", "enabled": False, "subscriptions": 0,
            "ticks_received": 0, "bars_closed": 0, "last_tick_at": None,
            "last_error": None, "orders_enabled": False,
        }
        self._aggregators: dict[str, MinuteBarAggregator] = {}
        self._bars: dict[str, deque[MinuteBar]] = {}

    def status(self) -> dict[str, object]:
        with self._lock:
            return dict(self._status)

    def bars(self, watch: PaperWatch) -> tuple[MinuteBar, ...]:
        with self._lock:
            return tuple(self._bars.get(watch.key, ()))

    async def start(self) -> None:
        if os.getenv("PAPER_SESSION_MONITOR_ENABLED", "false").strip().lower() != "true":
            return
        settings = StreamSettings.from_environment()
        if os.getenv("TRADING_MODE", "paper").strip().lower() != "paper" or os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower() == "true":
            self._set_status(state="BLOCKED", enabled=False, last_error="PAPER_MODE_REQUIRED")
            return
        if settings.kis.environment != "paper" or not settings.enabled or not settings.kis.app_key or not settings.kis.app_secret:
            self._set_status(state="BLOCKED", enabled=False, last_error="PAPER_KIS_STREAM_NOT_CONFIGURED")
            return
        try:
            watches = parse_watchlist(os.getenv("PAPER_WATCHLIST", ""))
        except ValueError:
            self._set_status(state="BLOCKED", enabled=False, last_error="PAPER_WATCHLIST_INVALID")
            return
        if not watches:
            self._set_status(state="BLOCKED", enabled=False, last_error="PAPER_WATCHLIST_EMPTY")
            return
        for watch in watches:
            self._aggregators[watch.key] = MinuteBarAggregator()
            self._bars[watch.key] = deque(maxlen=5_000)
        self._set_status(state="CONNECTING", enabled=True, subscriptions=len(watches), last_error=None)
        self._task = asyncio.create_task(self._run(watches, settings))

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.status().get("enabled"):
            self._set_status(state="STOPPED", enabled=False)

    async def _run(self, watches: tuple[PaperWatch, ...], settings: StreamSettings) -> None:
        delay = 1
        while True:
            try:
                key = await asyncio.to_thread(approval_key, settings.kis)
                import websockets

                async with websockets.connect(settings.websocket_url, ping_interval=30, close_timeout=3) as upstream:
                    for watch in watches:
                        await upstream.send(self._subscription_json(key, watch))
                    self._set_status(state="RUNNING", last_error=None)
                    delay = 1
                    async for frame in upstream:
                        self._consume(frame, watches)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._set_status(state="RECONNECTING", last_error="KIS_STREAM_UNAVAILABLE")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    @staticmethod
    def _subscription_json(key: str, watch: PaperWatch) -> str:
        import json

        return json.dumps(subscription_message(key, watch.market, watch.symbol, watch.exchange), separators=(",", ":"))

    def _consume(self, frame: str | bytes, watches: tuple[PaperWatch, ...]) -> None:
        if isinstance(frame, bytes):
            frame = frame.decode("utf-8", errors="replace")
        normalized = normalize_stream_frame(frame)
        if normalized.get("kind") != "data" or normalized.get("encrypted") != "0":
            return
        transaction_id = normalized.get("transaction_id", "")
        payload = "|".join(("0", transaction_id, normalized.get("count", "1"), normalized.get("payload", "")))
        for watch in watches:
            expected = "H0STCNT0" if watch.market == "KR" else "HDFSCNT0"
            if transaction_id != expected:
                continue
            try:
                ticks = parse_trade_frame(payload, watch.market, watch.exchange)
            except ValueError:
                continue
            for tick in ticks:
                if tick.symbol.upper() != watch.symbol.upper():
                    continue
                bar = self._aggregators[watch.key].add(tick)
                with self._lock:
                    self._status["ticks_received"] = int(self._status["ticks_received"]) + 1
                    self._status["last_tick_at"] = tick.timestamp.isoformat()
                    if bar is not None:
                        self._bars[watch.key].append(bar)
                        self._status["bars_closed"] = int(self._status["bars_closed"]) + 1

    def _set_status(self, **values: object) -> None:
        with self._lock:
            self._status.update(values)


paper_session_monitor = PaperSessionFeedMonitor()
