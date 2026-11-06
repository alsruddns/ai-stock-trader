"""Opt-in local paper-session monitor with separately gated market data feeds.

KR market data comes from the KIS paper WebSocket. US delayed market data can
use a separately enabled KIS REAL WebSocket while all order writes remain on
Spring's local paper ledger; this module has no KIS order client.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from . import snapshot_store
from .ai_pipeline import LocalModelRegistry
from .kis_stream import StreamSettings, approval_key, normalize_stream_frame, reply_to_pingpong, subscription_message
from .market_stream import MinuteBarAggregator, parse_trade_frame
from .quant import MinuteBar, QuantConfig, _atr, orb_vwap_entry_candidate
from .paper_automation import PaperAutomationReadiness, PaperAutoTrader, PaperEntryDecision
from .paper_broker import PaperBrokerError, SpringPaperBrokerClient
from .paper_ops import PaperOpsLimits, evaluate_paper_exit, evaluate_paper_gate, exchange_session_window, size_paper_order
from .paper_risk import baseline_capture_open, daily_loss_percent, paper_equity_snapshot


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
        self._stream_tasks: list[asyncio.Task] = []
        self._status: dict[str, object] = {
            "state": "DISABLED", "enabled": False, "subscriptions": 0,
            "ticks_received": 0, "bars_closed": 0, "last_tick_at": None,
            "last_tick_received_at": None,
            "ticks_received_by_feed": {"paper": 0, "real": 0},
            "last_tick_received_at_by_feed": {"paper": None, "real": None},
            "last_error": None, "orders_enabled": False,
            "automation_state": "DISABLED", "automation_error": None,
            "bar_storage_state": "DISABLED", "bar_storage_error": None,
        }
        self._aggregators: dict[str, MinuteBarAggregator] = {}
        self._bars: dict[str, deque[MinuteBar]] = {}
        self._decisions: dict[str, dict[str, object]] = {}
        self._automation_queue: asyncio.Queue[tuple[PaperWatch, MinuteBar, dict[str, object], dict[tuple[str, str], tuple[Decimal, datetime]]]] | None = None
        self._automation_task: asyncio.Task | None = None
        self._bar_storage_queue: asyncio.Queue[
            tuple[PaperWatch, MinuteBar, dict[str, object], dict[tuple[str, str], tuple[Decimal, datetime]]]
        ] | None = None
        self._bar_storage_task: asyncio.Task | None = None
        self._latest_quotes: dict[tuple[str, str], tuple[Decimal, datetime]] = {}

    def status(self) -> dict[str, object]:
        with self._lock:
            result = dict(self._status)
            result["decisions"] = {key: dict(value) for key, value in self._decisions.items()}
            result["ticks_received_by_feed"] = dict(self._status["ticks_received_by_feed"])
            result["last_tick_received_at_by_feed"] = dict(self._status["last_tick_received_at_by_feed"])
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
        settings = StreamSettings.from_environment("paper")
        if os.getenv("TRADING_MODE", "paper").strip().lower() != "paper" or os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower() == "true":
            self._set_status(state="BLOCKED", enabled=False, last_error="PAPER_MODE_REQUIRED")
            return
        if not settings.enabled:
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
        feed_groups: dict[str, tuple[tuple[PaperWatch, ...], StreamSettings]] = {}
        kr_watches = tuple(watch for watch in watches if watch.market == "KR")
        us_watches = tuple(watch for watch in watches if watch.market == "US")
        if kr_watches:
            if not settings.kis.app_key or not settings.kis.app_secret:
                self._set_status(state="BLOCKED", enabled=False, last_error="PAPER_KR_STREAM_NOT_CONFIGURED")
                return
            feed_groups["paper"] = (kr_watches, settings)
        if us_watches:
            if os.getenv("KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED", "false").strip().lower() != "true":
                self._set_status(state="BLOCKED", enabled=False, last_error="REAL_US_MARKET_DATA_STREAM_DISABLED")
                return
            us_settings = StreamSettings.from_environment("real")
            if not us_settings.kis.app_key or not us_settings.kis.app_secret:
                self._set_status(state="BLOCKED", enabled=False, last_error="REAL_US_MARKET_DATA_STREAM_NOT_CONFIGURED")
                return
            feed_groups["real"] = (us_watches, us_settings)
        persistence_enabled = snapshot_store.enabled()
        if (os.getenv("PAPER_AUTOTRADING_ENABLED", "false").strip().lower() == "true"
                and not persistence_enabled):
            self._set_status(
                state="BLOCKED", enabled=False, last_error="PAPER_HISTORY_PERSISTENCE_REQUIRED",
                bar_storage_state="DISABLED", automation_state="BLOCKED",
                automation_error="PAPER_HISTORY_PERSISTENCE_REQUIRED",
            )
            return
        for watch in watches:
            self._aggregators[watch.key] = MinuteBarAggregator()
            self._bars[watch.key] = deque(maxlen=5_000)
            self._decisions[watch.key] = {
                "market": watch.market, "symbol": watch.symbol, "exchange": watch.exchange,
                "signal": "HOLD", "reason": "WAITING_FOR_CLOSED_BARS", "bars_in_session": 0,
                "evaluated_at": None, "executable": False,
            }
        if persistence_enabled:
            restored_count = 0
            try:
                for watch in watches:
                    market_zone = ZoneInfo("Asia/Seoul" if watch.market == "KR" else "America/New_York")
                    session_date = datetime.now(market_zone).date()
                    restored = await asyncio.to_thread(
                        snapshot_store.load_session_minute_bars,
                        watch.market, watch.symbol, session_date,
                    )
                    if restored:
                        self._bars[watch.key].extend(restored)
                        self._evaluate_closed_history(watch, restored[-1])
                        restored_count += len(restored)
            except Exception:
                self._set_status(
                    state="BLOCKED", enabled=False, last_error="PAPER_BAR_HISTORY_RESTORE_FAILED",
                    bar_storage_state="ERROR", bar_storage_error="POSTGRES_BAR_RESTORE_FAILED",
                    automation_state="BLOCKED", automation_error="BAR_HISTORY_RESTORE_FAILED",
                )
                return
            self._bar_storage_queue = asyncio.Queue(maxsize=5_000)
            self._bar_storage_task = asyncio.create_task(self._bar_storage_loop())
            self._set_status(
                bar_storage_state="RUNNING", bar_storage_error=None,
                restored_bars=restored_count,
            )
        else:
            self._set_status(bar_storage_state="DISABLED", bar_storage_error=None)
        readiness = PaperAutomationReadiness.from_environment()
        if os.getenv("PAPER_AUTOTRADING_ENABLED", "false").strip().lower() == "true":
            if not readiness.enabled:
                self._set_status(automation_state="BLOCKED", automation_error="PAPER_AUTOMATION_SAFETY_SWITCH")
            else:
                try:
                    limits = PaperOpsLimits.from_environment()
                except ValueError:
                    self._set_status(automation_state="BLOCKED", automation_error="PAPER_RISK_SETTINGS_INVALID")
                else:
                    self._automation_queue = asyncio.Queue(maxsize=100)
                    self._automation_task = asyncio.create_task(self._automation_loop(limits))
                    self._set_status(automation_state="EXIT_MONITORING", automation_error=None)
        self._set_status(
            state="CONNECTING", enabled=True, subscriptions=len(watches), last_error=None,
            stream_connections={environment: "CONNECTING" for environment in feed_groups},
        )
        self._stream_tasks = [
            asyncio.create_task(self._run(group_watches, group_settings))
            for group_watches, group_settings in feed_groups.values()
        ]

    async def stop(self) -> None:
        stream_tasks = self._stream_tasks
        self._stream_tasks = []
        automation_task = self._automation_task
        self._automation_task = None
        storage_task = self._bar_storage_task
        self._bar_storage_task = None
        for task in stream_tasks:
            task.cancel()
        for task in stream_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if automation_task is not None:
            automation_task.cancel()
            try:
                await automation_task
            except asyncio.CancelledError:
                pass
        if storage_task is not None:
            queue = self._bar_storage_queue
            if queue is not None:
                try:
                    await asyncio.wait_for(queue.join(), timeout=5)
                except TimeoutError:
                    self._set_status(bar_storage_state="DRAIN_TIMEOUT", bar_storage_error="PENDING_BARS_NOT_FLUSHED")
            storage_task.cancel()
            try:
                await storage_task
            except asyncio.CancelledError:
                pass
            self._bar_storage_queue = None
        if self.status().get("enabled"):
            self._set_status(state="STOPPED", enabled=False)

    async def _bar_storage_loop(self) -> None:
        queue = self._bar_storage_queue
        if queue is None:
            return
        while True:
            watch, bar, decision, quotes = await queue.get()
            try:
                try:
                    await asyncio.to_thread(
                        snapshot_store.store_minute_bars, watch.market, watch.symbol, [bar],
                        source=("KIS_PAPER_WEBSOCKET" if watch.market == "KR" else "KIS_REAL_US_WEBSOCKET"),
                    )
                except Exception:
                    self._set_status(bar_storage_state="ERROR", bar_storage_error="POSTGRES_WRITE_FAILED")
                    if self._automation_queue is not None:
                        self._set_status(automation_state="BLOCKED", automation_error="BAR_PERSISTENCE_UNAVAILABLE")
                if self._automation_queue is not None:
                    try:
                        self._automation_queue.put_nowait((watch, bar, decision, quotes))
                    except asyncio.QueueFull:
                        self._set_status(automation_state="BLOCKED", automation_error="PAPER_WORKER_QUEUE_FULL")
            finally:
                queue.task_done()

    async def _run(self, watches: tuple[PaperWatch, ...], settings: StreamSettings) -> None:
        environment = settings.kis.environment
        delay = 1
        while True:
            connected_at: float | None = None
            try:
                key = await asyncio.to_thread(approval_key, settings.kis)
                import websockets

                async with websockets.connect(settings.websocket_url, ping_interval=30, close_timeout=3) as upstream:
                    connected_at = asyncio.get_running_loop().time()
                    for watch in watches:
                        await upstream.send(self._subscription_json(key, watch))
                    self._set_connection_state(environment, "RUNNING")
                    async for frame in upstream:
                        if await reply_to_pingpong(upstream, frame):
                            continue
                        self._consume(frame, watches)
            except asyncio.CancelledError:
                raise
            except Exception:
                connected_seconds = (asyncio.get_running_loop().time() - connected_at
                                     if connected_at is not None else 0)
                delay = self._next_reconnect_delay(delay, connected_seconds)
                self._set_connection_state(environment, "RECONNECTING")
                await asyncio.sleep(delay)

    def _set_connection_state(self, environment: str, state: str) -> None:
        with self._lock:
            connections = dict(self._status.get("stream_connections", {}))
            connections[environment] = state
            self._status["stream_connections"] = connections
            states = tuple(connections.values())
            if "RECONNECTING" in states:
                aggregate = "RECONNECTING"
            elif all(feed_state == "RUNNING" for feed_state in states):
                aggregate = "RUNNING"
            elif "RUNNING" in states:
                aggregate = "PARTIAL"
            else:
                aggregate = "CONNECTING"
            self._status["state"] = aggregate
            self._status["last_error"] = "KIS_STREAM_UNAVAILABLE" if aggregate == "RECONNECTING" else None

    @staticmethod
    def _next_reconnect_delay(current_delay: int, connected_seconds: float, *, stable_seconds: int = 60) -> int:
        """Keep exponential backoff across flapping connections; reset only after a stable session."""
        if current_delay < 1 or connected_seconds < 0 or stable_seconds < 1:
            raise ValueError("reconnect timing values must be positive")
        if connected_seconds >= stable_seconds:
            return 1
        return min(current_delay * 2, 30)

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
                    feed = "paper" if watch.market == "KR" else "real"
                    received_at = datetime.now().astimezone().isoformat()
                    feed_ticks = dict(self._status["ticks_received_by_feed"])
                    feed_ticks[feed] = int(feed_ticks.get(feed, 0)) + 1
                    feed_received_at = dict(self._status["last_tick_received_at_by_feed"])
                    feed_received_at[feed] = received_at
                    self._status["ticks_received_by_feed"] = feed_ticks
                    self._status["last_tick_received_at_by_feed"] = feed_received_at
                    self._latest_quotes[(watch.market, watch.symbol.upper())] = (tick.price, tick.timestamp)
                    self._status["ticks_received"] = int(self._status["ticks_received"]) + 1
                    self._status["last_tick_at"] = tick.timestamp.isoformat()
                    self._status["last_tick_received_at"] = received_at
                    if bar is not None:
                        self._record_closed_bar(watch, bar)
                        self._status["bars_closed"] = int(self._status["bars_closed"]) + 1
                        decision = dict(self._decisions[watch.key])
                        quotes = dict(self._latest_quotes)
                        if self._bar_storage_queue is not None:
                            # Persist market data independently from the opt-in paper order worker.
                            self._queue_bar_storage(watch, bar, decision, quotes)
                        elif self._automation_queue is not None:
                            try:
                                self._automation_queue.put_nowait((watch, bar, decision, quotes))
                            except asyncio.QueueFull:
                                self._set_status(automation_state="BLOCKED", automation_error="PAPER_WORKER_QUEUE_FULL")

    async def _automation_loop(self, limits: PaperOpsLimits) -> None:
        broker = SpringPaperBrokerClient()
        trader = PaperAutoTrader(broker)
        try:
            while True:
                queue = self._automation_queue
                if queue is None:
                    return
                watch, bar, decision, quotes = await queue.get()
                try:
                    baseline_result = await asyncio.to_thread(
                        self._capture_daily_baseline, broker, watch, bar, quotes, limits
                    )
                    if baseline_result is not None:
                        self._set_status(automation_state=str(baseline_result.get("state", "EXIT_MONITORING")),
                                         automation_error=baseline_result.get("reason"),
                                         daily_baseline=baseline_result)
                    result = await asyncio.to_thread(self._manage_strategy_exit, broker, trader, watch, bar, limits)
                    if result is not None:
                        self._set_status(automation_state=str(result.get("state", "EXIT_MONITORING")),
                                         automation_error=result.get("reason"),
                                         last_automation_result=result)
                    if (self._bar_storage_queue is not None
                            and self.status().get("bar_storage_state") != "RUNNING"):
                        entry_result = {"state": "BLOCKED", "reason": "BAR_PERSISTENCE_UNAVAILABLE"}
                    else:
                        entry_result = await asyncio.to_thread(
                            self._manage_strategy_entry, broker, trader, watch, bar, decision, quotes, limits
                        )
                    if entry_result is not None:
                        self._set_status(automation_state=str(entry_result.get("state", "EXIT_MONITORING")),
                                         automation_error=entry_result.get("reason"),
                                         last_automation_result=entry_result)
                except PaperBrokerError:
                    self._set_status(automation_state="BLOCKED", automation_error="LOCAL_PAPER_BROKER_UNAVAILABLE")
                except Exception:
                    self._set_status(automation_state="BLOCKED", automation_error="EXIT_EVALUATION_FAILED")
                finally:
                    queue.task_done()
        finally:
            trader.close()
            broker.close()

    @staticmethod
    def _manage_strategy_exit(broker: SpringPaperBrokerClient, trader: PaperAutoTrader,
                               watch: PaperWatch, bar: MinuteBar, limits: PaperOpsLimits) -> dict[str, object] | None:
        rows = broker.get_strategy_positions()
        position = next((row for row in rows
                         if str(row.get("market", "")).upper() == watch.market
                         and str(row.get("symbol", "")).upper() == watch.symbol.upper()), None)
        if position is None:
            return {"state": "EXIT_MONITORING", "reason": "NO_PERSISTED_STRATEGY_POSITION", "executable": False}
        try:
            stop = Decimal(str(position["stopPrice"]))
            target = Decimal(str(position["targetPrice"]))
            quantity = int(position["quantity"])
            currency = str(position["currency"]).upper()
        except (KeyError, ValueError, ArithmeticError):
            return {"state": "BLOCKED", "reason": "PERSISTED_EXIT_STATE_INVALID", "executable": False}
        window = exchange_session_window(watch.market, bar.timestamp)
        flatten = bool(window and window[1] - bar.timestamp.astimezone(timezone.utc)
                       <= timedelta(minutes=limits.flatten_before_close_minutes))
        exit_candidate = evaluate_paper_exit(bar, stop_price=stop, target_price=target, flatten_required=flatten)
        if exit_candidate is None:
            return {"state": "EXIT_MONITORING", "reason": "EXIT_LEVELS_NOT_REACHED", "executable": False}
        return trader.submit_exit(market=watch.market, symbol=watch.symbol, currency=currency,
                                  quantity=quantity, position_opened_at=str(position.get("openedAt", "")),
                                  bar_timestamp=bar.timestamp,
                                  exit_price=exit_candidate.price, reason=exit_candidate.reason,
                                  strategy_id=position.get("strategyId"), model_id=position.get("modelId"))

    def _manage_strategy_entry(
        self, broker: SpringPaperBrokerClient, trader: PaperAutoTrader, watch: PaperWatch, bar: MinuteBar,
        decision: dict[str, object], quotes: dict[tuple[str, str], tuple[Decimal, datetime]], limits: PaperOpsLimits,
    ) -> dict[str, object] | None:
        if decision.get("signal") != "BUY_CANDIDATE":
            return None
        now = datetime.now(timezone.utc)
        if bar.timestamp.tzinfo is None:
            return {"state": "BLOCKED", "reason": "SIGNAL_BAR_TIMESTAMP_INVALID", "executable": False}
        signal_bar_age = (now - bar.timestamp.astimezone(timezone.utc)).total_seconds()
        if signal_bar_age < -5 or signal_bar_age > limits.max_signal_bar_age_seconds:
            return {"state": "BLOCKED", "reason": "SIGNAL_BAR_STALE", "executable": False}
        currency = "KRW" if watch.market == "KR" else "USD"
        try:
            positions = broker.get_positions()
            cash_balances = broker.get_cash_balances()
            baselines = broker.get_daily_risk_baselines(bar.timestamp.date())
        except PaperBrokerError:
            return {"state": "BLOCKED", "reason": "LOCAL_PAPER_RISK_DATA_UNAVAILABLE", "executable": False}

        matching_baseline = next((row for row in baselines
                                  if str(row.get("currency", "")).upper() == currency), None)
        snapshot = paper_equity_snapshot(currency, cash_balances=cash_balances, positions=positions,
                                         quotes=quotes, now=now, max_quote_age_seconds=limits.max_quote_age_seconds)
        if matching_baseline is None:
            return {"state": "BLOCKED", "reason": "DAILY_BASELINE_MISSING", "executable": False}
        try:
            baseline_equity = Decimal(str(matching_baseline["equity"]))
        except (KeyError, ValueError, ArithmeticError):
            return {"state": "BLOCKED", "reason": "DAILY_BASELINE_INVALID", "executable": False}
        loss_pct = daily_loss_percent(baseline_equity, snapshot.equity)
        latest_quote = quotes.get((watch.market, watch.symbol.upper()))
        try:
            operations = broker.get_operations_summary()
            consecutive_losses = int(operations["consecutiveLosingTrades"])
            open_orders = int(operations["openOrders"])
            if consecutive_losses < 0 or open_orders < 0:
                raise ValueError
        except (PaperBrokerError, KeyError, TypeError, ValueError):
            return {"state": "BLOCKED", "reason": "LOCAL_PAPER_RISK_SUMMARY_UNAVAILABLE", "executable": False}
        if open_orders > 0:
            return {"state": "BLOCKED", "reason": "OPEN_PAPER_ORDER_REQUIRES_RECONCILIATION", "executable": False}
        is_approved_candidate = bool(decision.get("model_id")) and decision.get("signal") == "BUY_CANDIDATE"
        feed_healthy = self.status().get("state") == "RUNNING"
        gate = evaluate_paper_gate(
            watch.market, now, latest_quote[1] if latest_quote else None,
            data_feed_healthy=feed_healthy, model_available=is_approved_candidate,
            model_approved=is_approved_candidate, broker_healthy=True,
            daily_loss_pct=loss_pct if loss_pct is not None else limits.max_daily_loss_pct,
            consecutive_losses=consecutive_losses,
            open_positions=len([item for item in positions if int(item.get("quantity", 0)) > 0]),
            limits=limits,
        )
        if not gate.new_entries_allowed:
            return {"state": "BLOCKED", "reason": ",".join(gate.reasons) or "PAPER_RISK_GATE_BLOCKED", "executable": False}
        if snapshot.equity is None:
            return {"state": "BLOCKED", "reason": snapshot.reason or "CURRENT_EQUITY_UNAVAILABLE", "executable": False}
        if any(str(item.get("market", "")).upper() == watch.market
               and str(item.get("symbol", "")).upper() == watch.symbol.upper()
               and int(item.get("quantity", 0)) > 0 for item in positions):
            return {"state": "BLOCKED", "reason": "SYMBOL_ALREADY_HELD", "executable": False}
        cash_row = next((item for item in cash_balances
                         if str(item.get("currency", "")).upper() == currency), None)
        if cash_row is None:
            return {"state": "BLOCKED", "reason": "PAPER_CASH_MISSING", "executable": False}
        history = list(self.bars(watch))
        try:
            atr = _atr(history, QuantConfig().atr_period)
            max_notional = Decimal(os.getenv("MAX_ORDER_NOTIONAL_KRW" if currency == "KRW" else "MAX_ORDER_NOTIONAL_USD",
                                            "1000000" if currency == "KRW" else "100"))
            sizing = size_paper_order(price=bar.close, atr=atr, equity=snapshot.equity,
                                      available_cash=Decimal(str(cash_row["balance"])),
                                      max_order_notional=max_notional, limits=limits)
        except (KeyError, ValueError, ArithmeticError):
            return {"state": "BLOCKED", "reason": "PAPER_ORDER_SIZING_FAILED", "executable": False}
        if sizing is None:
            return {"state": "BLOCKED", "reason": "PAPER_ORDER_SIZE_ZERO", "executable": False}
        target = bar.close + sizing.stop_distance * QuantConfig().reward_risk_multiple
        entry_decision = PaperEntryDecision(
            market=watch.market, symbol=watch.symbol, currency=currency, signal="BUY_CANDIDATE",
            model_approved=is_approved_candidate, gate=gate, sizing=sizing, bar_timestamp=bar.timestamp,
            limit_price=bar.close, stop_price=bar.close - sizing.stop_distance, target_price=target,
            exit_manager_ready=True, model_id=str(decision["model_id"]),
        )
        return trader.submit_entry(entry_decision)

    @staticmethod
    def _capture_daily_baseline(
        broker: SpringPaperBrokerClient, watch: PaperWatch, bar: MinuteBar,
        quotes: dict[tuple[str, str], tuple[Decimal, datetime]], limits: PaperOpsLimits,
    ) -> dict[str, object] | None:
        currency = "KRW" if watch.market == "KR" else "USD"
        session_date = bar.timestamp.date()
        try:
            baselines = broker.get_daily_risk_baselines(session_date)
            if any(str(row.get("currency", "")).upper() == currency for row in baselines):
                return {"state": "BASELINE_PRESENT", "currency": currency,
                        "session_date": session_date.isoformat(), "executable": False}
            positions = broker.get_positions()
            cash_balances = broker.get_cash_balances()
        except PaperBrokerError:
            return {"state": "BLOCKED", "reason": "DAILY_BASELINE_SOURCE_UNAVAILABLE", "executable": False}
        session = exchange_session_window(watch.market, bar.timestamp)
        now = datetime.now(timezone.utc)
        if not session or not baseline_capture_open(market_open=session[0], now=now):
            return {"state": "BLOCKED", "reason": "DAILY_BASELINE_CAPTURE_WINDOW_MISSED", "executable": False}
        snapshot = paper_equity_snapshot(currency, cash_balances=cash_balances, positions=positions,
                                         quotes=quotes, now=now, max_quote_age_seconds=limits.max_quote_age_seconds)
        if snapshot.equity is None:
            return {"state": "BLOCKED", "reason": snapshot.reason or "DAILY_BASELINE_EQUITY_UNAVAILABLE",
                    "executable": False}
        try:
            broker.record_daily_risk_baseline(session_date, currency, snapshot.equity)
        except PaperBrokerError:
            return {"state": "BLOCKED", "reason": "DAILY_BASELINE_WRITE_FAILED", "executable": False}
        return {"state": "BASELINE_CAPTURED", "currency": currency, "session_date": session_date.isoformat(),
                "equity": str(snapshot.equity), "executable": False}

    def _record_closed_bar(self, watch: PaperWatch, bar: MinuteBar) -> None:
        history = self._bars[watch.key]
        session_day = bar.timestamp.date()
        if history and history[-1].timestamp.date() != session_day:
            history.clear()
        history.append(bar)
        self._evaluate_closed_history(watch, bar)

    def _evaluate_closed_history(self, watch: PaperWatch, bar: MinuteBar) -> None:
        history = self._bars[watch.key]
        signal = "HOLD"
        reason = "INSUFFICIENT_SESSION_HISTORY"
        model_id: str | None = None
        if len(history) >= max(QuantConfig().opening_range_bars, QuantConfig().ema_period,
                               QuantConfig().atr_period, QuantConfig().relative_volume_period):
            try:
                quant_candidate = orb_vwap_entry_candidate(
                    list(history), QuantConfig(), market=watch.market,
                )
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

    def _queue_bar_storage(
        self, watch: PaperWatch, bar: MinuteBar, decision: dict[str, object],
        quotes: dict[tuple[str, str], tuple[Decimal, datetime]],
    ) -> None:
        queue = self._bar_storage_queue
        if queue is None:
            return
        try:
            queue.put_nowait((watch, bar, decision, quotes))
        except asyncio.QueueFull:
            self._set_status(bar_storage_state="ERROR", bar_storage_error="BAR_STORAGE_QUEUE_FULL")
            if self._automation_queue is not None:
                try:
                    self._automation_queue.put_nowait((watch, bar, decision, quotes))
                except asyncio.QueueFull:
                    self._set_status(automation_state="BLOCKED", automation_error="PAPER_WORKER_QUEUE_FULL")

    def _set_status(self, **values: object) -> None:
        with self._lock:
            self._status.update(values)


paper_session_monitor = PaperSessionFeedMonitor()
