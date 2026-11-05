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

from .ai_pipeline import LocalModelRegistry
from .kis_stream import StreamSettings, approval_key, normalize_stream_frame, subscription_message
from .market_stream import MinuteBarAggregator, parse_trade_frame
from .quant import MinuteBar, QuantConfig, orb_vwap_entry_candidate


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
        self._decisions: dict[str, dict[str, object]] = {}

    def status(self) -> dict[str, object]:
        with self._lock:
            result = dict(self._status)
            result["decisions"] = {key: dict(value) for key, value in self._decisions.items()}
            return result

    def bars(self, watch: PaperWatch) -> tuple[MinuteBar, ...]:
        with self._lock:
            return tuple(self._bars.get(watch.key, ()))

    def decisions(self) -> dict[str, dict[str, object]]:
        """Return latest advisory quant decisions; none are executable orders."""
        with self._lock:
            return {key: dict(value) for key, value in self._decisions.items()}

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
            self._decisions[watch.key] = {
                "market": watch.market, "symbol": watch.symbol, "exchange": watch.exchange,
                "signal": "HOLD", "reason": "WAITING_FOR_CLOSED_BARS", "bars_in_session": 0,
                "evaluated_at": None, "executable": False,
            }
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
                        self._record_closed_bar(watch, bar)
                        self._status["bars_closed"] = int(self._status["bars_closed"]) + 1

    def _record_closed_bar(self, watch: PaperWatch, bar: MinuteBar) -> None:
        history = self._bars[watch.key]
        session_day = bar.timestamp.date()
        if history and history[-1].timestamp.date() != session_day:
            history.clear()
        history.append(bar)
        signal = "HOLD"
        reason = "INSUFFICIENT_SESSION_HISTORY"
        model_id: str | None = None
        if len(history) >= max(QuantConfig().opening_range_bars, QuantConfig().ema_period,
                               QuantConfig().atr_period, QuantConfig().relative_volume_period):
            try:
                quant_candidate = orb_vwap_entry_candidate(list(history), QuantConfig())
            except ValueError:
                quant_candidate = False
                reason = "BAR_SEQUENCE_INVALID"
            else:
                reason = "STRATEGY_FILTERS_NOT_MET"
            if quant_candidate:
                try:
                    registry = LocalModelRegistry(os.getenv("AI_MODEL_REGISTRY_PATH", ".local/models"))
                    metadata = registry.active_approved_model(watch.market)
                    if metadata is None:
                        reason = "AI_MODEL_NOT_APPROVED_FOR_MARKET"
                    else:
                        prediction = registry.predict_preview(metadata["model_id"], list(history), market=watch.market)
                        model_id = metadata["model_id"]
                        if prediction["probability_positive"] >= prediction["threshold"]:
                            signal, reason = "BUY_CANDIDATE", "QUANT_AND_APPROVED_AI_CANDIDATE"
                        else:
                            reason = "AI_THRESHOLD_NOT_MET"
                except Exception:
                    # Optional model packages, corrupt artifacts, and model I/O all fail closed.
                    signal, reason, model_id = "HOLD", "AI_INFERENCE_UNAVAILABLE", None
        self._decisions[watch.key] = {
            "market": watch.market, "symbol": watch.symbol, "exchange": watch.exchange,
            "signal": signal, "reason": reason, "bars_in_session": len(history),
            "evaluated_at": bar.timestamp.isoformat(), "model_id": model_id, "executable": False,
        }

    def _set_status(self, **values: object) -> None:
        with self._lock:
            self._status.update(values)


paper_session_monitor = PaperSessionFeedMonitor()
