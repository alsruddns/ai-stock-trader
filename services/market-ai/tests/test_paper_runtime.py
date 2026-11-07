import asyncio
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
import json
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.paper_runtime import PaperSessionFeedMonitor, PaperWatch, next_market_session_open, parse_watchlist
from app.paper_automation import PaperAutoTrader
from app.paper_ops import PaperOpsLimits
from app.market_stream import MinuteBarAggregator
from app.quant import MinuteBar
from app.real_signal_orders import RealSignalOrderOutcomeUnknown, real_signal_idempotency_key


def test_watchlist_normalizes_and_deduplicates_valid_symbols() -> None:
    watches = parse_watchlist("KR:005930;US:aapl:nas;KR:005930")
    assert [watch.key for watch in watches] == ["KR:005930:", "US:AAPL:NAS"]


def test_watchlist_respects_kis_forty_subscription_limit() -> None:
    entries = ";".join(f"KR:{index:06d}" for index in range(1, 42))
    with pytest.raises(ValueError, match="at most 40"):
        parse_watchlist(entries)


@pytest.mark.parametrize(
    ("market", "expected_open"),
    [("KR", "2026-11-09T00:00:00+00:00"), ("US", "2026-11-09T14:30:00+00:00")],
)
def test_next_market_session_open_skips_weekend(market: str, expected_open: str) -> None:
    now = datetime(2026, 11, 7, 12, tzinfo=ZoneInfo("Asia/Seoul"))
    assert next_market_session_open(market, now).isoformat() == expected_open


def test_candidate_auto_refresh_selects_turnover_without_requiring_premarket_spread(monkeypatch) -> None:
    from app import main

    selected: list[tuple[PaperWatch, ...]] = []
    class Client:
        closed = False

        def get_ranked_candidates(self, market, *, exchange, limit, minimum_turnover, maximum_spread_bps):
            assert (market, exchange, limit, minimum_turnover) == ("KR", None, 10, Decimal("1000"))
            assert maximum_spread_bps == Decimal("15")
            return [
                SimpleNamespace(market="KR", symbol="000660", exchange=None, price=Decimal("100"),
                                volume=10, turnover=Decimal("1000"), passes_market_filter=False),
                SimpleNamespace(market="KR", symbol="000001", exchange=None, price=Decimal("100"),
                                volume=10, turnover=Decimal("999"), passes_market_filter=True),
            ]

        def close(self):
            self.closed = True

    client = Client()
    monkeypatch.setattr(main, "get_kis_candidate_client", lambda _market: client)
    monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "false")
    monkeypatch.setenv("PAPER_CANDIDATE_MIN_TURNOVER_KRW", "1000")
    monkeypatch.delenv("PAPER_CANDIDATE_US_EXCHANGE", raising=False)
    runtime = PaperSessionFeedMonitor()

    async def refresh(market, desired):
        assert market == "KR"
        selected.append(desired)
        return {"added": [watch.key for watch in desired], "removed": [], "preserved": 0,
                "open_orders": 0, "subscriptions": 1}

    monkeypatch.setattr(runtime, "refresh_discovered_watches", refresh)
    result = asyncio.run(runtime._refresh_candidates_once("KR"))
    assert [watch.symbol for watch in selected[0]] == ["000660"]
    assert client.closed is True
    assert result["selected"] == 1


def test_candidate_auto_refresh_fails_closed_when_threshold_missing(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "false")
    monkeypatch.delenv("PAPER_CANDIDATE_MIN_TURNOVER_KRW", raising=False)
    runtime = PaperSessionFeedMonitor()
    with pytest.raises(ValueError, match="threshold is required"):
        asyncio.run(runtime._refresh_candidates_once("KR"))


def test_monitor_adds_screened_watch_to_live_feed_subscription_queue(monkeypatch) -> None:
    class FakeUpstream:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(json.loads(message))

    async def scenario():
        runtime = PaperSessionFeedMonitor()
        runtime._watches_by_feed = {"paper": [PaperWatch("KR", "005930")]}
        runtime._subscription_update_queues = {"paper": asyncio.Queue()}
        runtime._set_status(
            state="RUNNING", enabled=True, subscriptions=1,
            stream_connections={"paper": "RUNNING"},
        )
        async def prepare(_watch):
            return None
        monkeypatch.setattr(runtime, "_prepare_watch", prepare)
        upstream = FakeUpstream()
        writer = asyncio.create_task(
            runtime._write_subscription_updates(upstream, "approval", "paper", {"KR:005930:"})
        )
        try:
            result = await runtime.add_watches((PaperWatch("KR", "000660"),))
            await asyncio.wait_for(runtime._subscription_update_queues["paper"].join(), timeout=1)
        finally:
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass

        assert result == {"added": ["KR:000660:"], "subscriptions": 2}
        assert upstream.sent[0]["header"]["tr_type"] == "1"
        assert upstream.sent[0]["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "000660"}
        assert runtime._watches_by_feed["paper"][-1] == PaperWatch("KR", "000660")

    asyncio.run(scenario())


def test_monitor_refuses_dynamic_watch_when_market_feed_is_not_running() -> None:
    async def scenario():
        runtime = PaperSessionFeedMonitor()
        runtime._watches_by_feed = {"paper": [PaperWatch("KR", "005930")]}
        runtime._subscription_update_queues = {"paper": asyncio.Queue()}
        runtime._set_status(
            state="RECONNECTING", enabled=True, subscriptions=1,
            stream_connections={"paper": "RECONNECTING"},
        )
        with pytest.raises(RuntimeError, match="not running"):
            await runtime.add_watches((PaperWatch("KR", "000660"),))
        assert [watch.symbol for watch in runtime._watches_by_feed["paper"]] == ["005930"]

    asyncio.run(scenario())


def test_monitor_unsubscribes_only_a_candidate_watch_after_safe_reconciliation(monkeypatch) -> None:
    async def scenario():
        runtime = PaperSessionFeedMonitor()
        manual = PaperWatch("KR", "005930")
        candidate = PaperWatch("KR", "000660")
        runtime._watches_by_feed = {"paper": [manual, candidate]}
        runtime._subscription_update_queues = {"paper": asyncio.Queue()}
        runtime._discovered_watch_keys = {candidate.key}
        runtime._aggregators[candidate.key] = MinuteBarAggregator()
        runtime._bars[candidate.key] = deque(maxlen=5_000)
        runtime._decisions[candidate.key] = {"signal": "HOLD"}
        runtime._latest_quotes[("KR", candidate.symbol)] = (Decimal("100"), datetime.now().astimezone())
        runtime._set_status(
            state="RUNNING", enabled=True, subscriptions=2,
            stream_connections={"paper": "RUNNING"},
        )
        class FakeUpstream:
            def __init__(self):
                self.sent = []

            async def send(self, message):
                self.sent.append(json.loads(message))

        upstream = FakeUpstream()
        writer = asyncio.create_task(
            runtime._write_subscription_updates(upstream, "approval", "paper", {manual.key, candidate.key})
        )
        try:
            removed = await runtime.remove_discovered_watches((candidate,))
            await asyncio.wait_for(runtime._subscription_update_queues["paper"].join(), timeout=1)
        finally:
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass

        assert removed == [candidate.key]
        assert upstream.sent[0]["header"]["tr_type"] == "2"
        assert upstream.sent[0]["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "000660"}
        assert runtime._watches_by_feed["paper"] == [manual]
        assert candidate.key not in runtime._bars
        assert ("KR", candidate.symbol) not in runtime._latest_quotes

    asyncio.run(scenario())


def test_candidate_refresh_preserves_old_subscriptions_if_any_order_is_open(monkeypatch) -> None:
    class Broker:
        def get_watchlist_protection_state(self):
            return {"open_orders": 1, "held_symbols": set()}

        def close(self):
            pass

    async def scenario():
        monkeypatch.setattr("app.paper_runtime.SpringPaperBrokerClient", Broker)
        monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "false")
        runtime = PaperSessionFeedMonitor()
        old = PaperWatch("KR", "000660")
        current = PaperWatch("KR", "005930")
        runtime._watches_by_feed = {"paper": [old, current]}
        runtime._subscription_update_queues = {"paper": asyncio.Queue()}
        runtime._discovered_watch_keys = {old.key}
        runtime._set_status(
            state="RUNNING", enabled=True, subscriptions=2,
            stream_connections={"paper": "RUNNING"},
        )
        async def prepare(_watch):
            return None
        monkeypatch.setattr(runtime, "_prepare_watch", prepare)
        result = await runtime.refresh_discovered_watches("KR", (current,))
        assert result["removed"] == []
        assert result["preserved"] == 1
        assert old.key in runtime._discovered_watch_keys

    asyncio.run(scenario())


def test_candidate_refresh_keeps_held_symbol_and_unsubscribes_only_unused_candidate(monkeypatch) -> None:
    class Broker:
        def get_watchlist_protection_state(self):
            return {"open_orders": 0, "held_symbols": {("KR", "000660")}}

        def close(self):
            pass

    async def scenario():
        monkeypatch.setattr("app.paper_runtime.SpringPaperBrokerClient", Broker)
        monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "false")
        runtime = PaperSessionFeedMonitor()
        held = PaperWatch("KR", "000660")
        stale = PaperWatch("KR", "000001")
        current = PaperWatch("KR", "005930")
        runtime._watches_by_feed = {"paper": [held, stale, current]}
        runtime._subscription_update_queues = {"paper": asyncio.Queue()}
        runtime._discovered_watch_keys = {held.key, stale.key}
        runtime._set_status(
            state="RUNNING", enabled=True, subscriptions=3,
            stream_connections={"paper": "RUNNING"},
        )
        class FakeUpstream:
            def __init__(self):
                self.sent = []

            async def send(self, message):
                self.sent.append(json.loads(message))

        upstream = FakeUpstream()
        writer = asyncio.create_task(
            runtime._write_subscription_updates(
                upstream, "approval", "paper", {held.key, stale.key, current.key},
            )
        )
        try:
            result = await runtime.refresh_discovered_watches("KR", (current,))
            await asyncio.wait_for(runtime._subscription_update_queues["paper"].join(), timeout=1)
        finally:
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass

        assert result["preserved"] == 1
        assert result["removed"] == [stale.key]
        assert held in runtime._watches_by_feed["paper"]
        assert stale not in runtime._watches_by_feed["paper"]
        assert upstream.sent[0]["header"]["tr_type"] == "2"
        assert upstream.sent[0]["body"]["input"] == {"tr_id": "H0STCNT0", "tr_key": "000001"}

    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["KR:SAMSUNG", "US:AAPL", "US:AAPL:LSE", "KR:005930:KRX"])
def test_watchlist_rejects_unsupported_market_codes(value: str) -> None:
    with pytest.raises(ValueError):
        parse_watchlist(value)


def test_feed_monitor_remains_disabled_without_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("PAPER_SESSION_MONITOR_ENABLED", raising=False)
    runtime = PaperSessionFeedMonitor()

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "DISABLED"
    assert status["enabled"] is False
    assert status["orders_enabled"] is False
    assert status["automation_state"] == "DISABLED"


def test_closed_paper_websocket_bars_are_written_off_loop_with_spread(monkeypatch) -> None:
    from app import snapshot_store

    stored = []
    monkeypatch.setattr(snapshot_store, "store_minute_bars", lambda *args, **kwargs: stored.append((args, kwargs)))
    runtime = PaperSessionFeedMonitor()
    watch = parse_watchlist("KR:005930")[0]
    runtime._bars[watch.key] = deque()
    runtime._decisions[watch.key] = {}
    runtime._bar_storage_queue = asyncio.Queue()
    runtime._set_status(bar_storage_state="RUNNING")
    bar = MinuteBar(
        datetime.fromisoformat("2026-11-05T09:00:00+09:00"), Decimal("100"), Decimal("101"),
        Decimal("99"), Decimal("100.5"), 1000, Decimal("4.5"),
    )

    async def run_storage():
        worker = asyncio.create_task(runtime._bar_storage_loop())
        runtime._record_closed_bar(watch, bar)
        runtime._queue_bar_storage(watch, bar, {}, {})
        await asyncio.wait_for(runtime._bar_storage_queue.join(), timeout=1)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

    asyncio.run(run_storage())

    assert stored == [(("KR", "005930", [bar]), {"source": "KIS_PAPER_WEBSOCKET"})]
    assert runtime.status()["bar_storage_state"] == "RUNNING"


def test_partial_session_restore_cannot_create_a_buy_candidate() -> None:
    runtime = PaperSessionFeedMonitor()
    watch = parse_watchlist("KR:005930")[0]
    start = datetime.fromisoformat("2026-11-05T10:12:00+09:00")
    runtime._bars[watch.key] = deque(
        MinuteBar(start + timedelta(minutes=index), Decimal("100"), Decimal("101"),
                  Decimal("99"), Decimal("100.5"), 1000, Decimal("5"))
        for index in range(21)
    )
    runtime._decisions[watch.key] = {}

    runtime._evaluate_closed_history(watch, runtime._bars[watch.key][-1])

    decision = runtime.decisions()[watch.key]
    assert decision["signal"] == "HOLD"
    assert decision["reason"] == "BAR_SEQUENCE_INVALID"
    assert decision["model_id"] is None
    assert decision["executable"] is False


@pytest.mark.parametrize(("market", "symbol", "exchange", "transaction_id", "feed"), [
    ("KR", "005930", None, "H0STCNT0", "paper"),
    ("US", "AAPL", "NAS", "HDFSCNT0", "real"),
])
def test_collector_persists_bars_when_paper_order_worker_is_disabled(
    monkeypatch, market, symbol, exchange, transaction_id, feed,
) -> None:
    from app.market_stream import TradeTick

    watch_value = f"KR:{symbol}" if market == "KR" else f"US:{symbol}:{exchange}"
    watch = parse_watchlist(watch_value)[0]
    bar = MinuteBar(
        datetime.fromisoformat("2026-11-05T09:00:00+09:00"), Decimal("100"), Decimal("101"),
        Decimal("99"), Decimal("100.5"), 1000, Decimal("4.5"),
    )
    tick = TradeTick(market, symbol, bar.timestamp, bar.close, 10, Decimal("100"), Decimal("101"))
    runtime = PaperSessionFeedMonitor()
    runtime._aggregators[watch.key] = SimpleNamespace(add=lambda _tick: bar)
    runtime._bars[watch.key] = deque()
    runtime._decisions[watch.key] = {"signal": "HOLD", "executable": False}
    runtime._bar_storage_queue = asyncio.Queue()
    runtime._set_status(bar_storage_state="RUNNING")
    monkeypatch.setattr(
        "app.paper_runtime.normalize_stream_frame",
        lambda _frame: {"kind": "data", "encrypted": "0", "transaction_id": transaction_id, "count": "1", "payload": "tick"},
    )
    monkeypatch.setattr("app.paper_runtime.parse_trade_frame", lambda *_args: [tick])

    runtime._consume("fixture", (watch,))

    assert runtime._automation_queue is None
    assert runtime._bar_storage_queue.qsize() == 1
    queued_watch, queued_bar, _decision, _quotes = runtime._bar_storage_queue.get_nowait()
    assert queued_watch == watch
    assert queued_bar == bar
    status = runtime.status()
    assert status["orders_enabled"] is False
    assert datetime.fromisoformat(status["last_tick_received_at"]).tzinfo is not None
    assert status["ticks_received_by_feed"][feed] == 1
    other_feed = "real" if feed == "paper" else "paper"
    assert status["ticks_received_by_feed"][other_feed] == 0
    assert datetime.fromisoformat(status["last_tick_received_at_by_feed"][feed]).tzinfo is not None
    assert status["last_tick_received_at_by_feed"][other_feed] is None
    assert status["bars_closed_by_feed"][feed] == 1
    assert status["bars_closed_by_feed"][other_feed] == 0


def test_bar_storage_failure_is_visible_and_blocks_paper_automation(monkeypatch) -> None:
    from app import snapshot_store

    def fail_write(*_args, **_kwargs):
        raise RuntimeError("database details must not be exposed")

    monkeypatch.setattr(snapshot_store, "store_minute_bars", fail_write)
    runtime = PaperSessionFeedMonitor()
    watch = parse_watchlist("KR:005930")[0]
    runtime._bars[watch.key] = deque()
    runtime._decisions[watch.key] = {}
    runtime._bar_storage_queue = asyncio.Queue()
    runtime._automation_queue = asyncio.Queue()
    runtime._set_status(bar_storage_state="RUNNING")
    bar = MinuteBar(
        datetime.fromisoformat("2026-11-05T09:00:00+09:00"), Decimal("100"), Decimal("101"),
        Decimal("99"), Decimal("100.5"), 1000, Decimal("4.5"),
    )

    async def run_storage():
        worker = asyncio.create_task(runtime._bar_storage_loop())
        runtime._record_closed_bar(watch, bar)
        runtime._queue_bar_storage(watch, bar, {}, {})
        await asyncio.wait_for(runtime._bar_storage_queue.join(), timeout=1)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

    asyncio.run(run_storage())

    assert runtime.status()["bar_storage_state"] == "ERROR"
    assert runtime.status()["bar_storage_error"] == "POSTGRES_WRITE_FAILED"
    assert runtime.status()["automation_state"] == "BLOCKED"
    assert runtime.status()["automation_error"] == "BAR_PERSISTENCE_UNAVAILABLE"
    assert "database details" not in str(runtime.status())


def test_websocket_reconnect_backoff_survives_short_lived_connections() -> None:
    delay = 1
    delays = []
    for _ in range(5):
        delay = PaperSessionFeedMonitor._next_reconnect_delay(delay, connected_seconds=2)
        delays.append(delay)

    assert delays == [2, 4, 8, 16, 30]
    assert PaperSessionFeedMonitor._next_reconnect_delay(30, connected_seconds=60) == 1


def test_multi_feed_status_only_reports_running_when_all_streams_are_connected() -> None:
    runtime = PaperSessionFeedMonitor()
    runtime._set_status(stream_connections={"paper": "CONNECTING", "real": "CONNECTING"})

    runtime._set_connection_state("paper", "RUNNING")
    assert runtime.status()["state"] == "PARTIAL"

    runtime._set_connection_state("real", "RUNNING")
    assert runtime.status()["state"] == "RUNNING"

    runtime._set_connection_state("real", "RECONNECTING")
    assert runtime.status()["state"] == "RECONNECTING"
    assert runtime.status()["last_error"] == "KIS_STREAM_UNAVAILABLE"


def test_websocket_run_keeps_backoff_across_repeated_short_connections(monkeypatch) -> None:
    class DroppingConnection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def send(self, _message):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("simulated disconnect")

    delays = []

    async def record_sleep(delay):
        delays.append(delay)
        if len(delays) == 5:
            raise asyncio.CancelledError

    monkeypatch.setattr("app.paper_runtime.approval_key", lambda _settings: "mock-approval")
    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(
        connect=lambda *_args, **_kwargs: DroppingConnection(),
    ))
    monkeypatch.setattr("app.paper_runtime.asyncio.sleep", record_sleep)

    runtime = PaperSessionFeedMonitor()
    settings = SimpleNamespace(kis=SimpleNamespace(environment="paper"), websocket_url="ws://mock.invalid")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime._run((), settings))

    assert delays == [2, 4, 8, 16, 30]
    assert runtime.status()["state"] == "RECONNECTING"


def test_live_mode_blocks_paper_session_monitor(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_SESSION_MONITOR_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    runtime = PaperSessionFeedMonitor()

    asyncio.run(runtime.start())

    assert runtime.status()["state"] == "BLOCKED"
    assert runtime.status()["last_error"] == "PAPER_MODE_REQUIRED"


def test_real_signal_monitor_requires_its_own_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "real")
    monkeypatch.setenv("PAPER_SESSION_MONITOR_ENABLED", "true")
    monkeypatch.setenv("REAL_SIGNAL_MONITOR_ENABLED", "false")
    runtime = PaperSessionFeedMonitor()

    asyncio.run(runtime.start())

    assert runtime.status()["state"] == "DISABLED"
    assert runtime._real_signal_queue is None
    assert runtime._real_signal_task is None


def test_real_signal_monitor_uses_real_feed_for_both_markets_without_order_gates(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "real")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("REAL_SIGNAL_MONITOR_ENABLED", "true")
    monkeypatch.setenv("PAPER_SESSION_MONITOR_ENABLED", "false")
    monkeypatch.setenv("KIS_WEBSOCKET_ENABLED", "true")
    monkeypatch.setenv("KIS_REAL_APP_KEY", "real-key")
    monkeypatch.setenv("KIS_REAL_APP_SECRET", "real-secret")
    monkeypatch.setenv("KIS_REAL_ACCOUNT_NUMBER", "1234567801")
    monkeypatch.setenv("PAPER_WATCHLIST", "KR:005930;US:AAPL:NAS")
    for name in (
        "KIS_REAL_ORDERS_ENABLED", "KIS_ACCOUNT_READ_ENABLED", "KIS_REAL_ACCOUNT_READ_ENABLED",
        "REAL_PROTECTIVE_EXIT_MANAGER_ENABLED", "KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED",
        "KIS_REAL_RECONCILIATION_ENABLED", "REAL_SIGNAL_SUBMISSION_ENABLED",
    ):
        monkeypatch.setenv(name, "false")
    runtime = PaperSessionFeedMonitor()
    feeds = []

    async def hold_feed(watches, settings):
        feeds.append((tuple(watches), settings.kis.environment))
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_run", hold_feed)

    async def scenario():
        await runtime.start()
        await asyncio.sleep(0)
        status = runtime.status()
        await runtime.stop()
        return status

    status = asyncio.run(scenario())

    assert len(feeds) == 1
    assert {watch.market for watch in feeds[0][0]} == {"KR", "US"}
    assert feeds[0][1] == "real"
    assert status["mode"] == "REAL_SIGNAL"
    assert status["real_signal_state"] == "LOCKED_BY_SAFETY_GATES"
    assert status["orders_enabled"] is False
    assert runtime._real_signal_client is None


def test_real_signal_submission_stays_locked_without_real_rest_evidence_settings(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "real")
    monkeypatch.setenv("REAL_SIGNAL_MONITOR_ENABLED", "true")
    monkeypatch.setenv("KIS_WEBSOCKET_ENABLED", "true")
    monkeypatch.setenv("KIS_REAL_APP_KEY", "real-key")
    monkeypatch.setenv("KIS_REAL_APP_SECRET", "real-secret")
    monkeypatch.setenv("KIS_REAL_ACCOUNT_NUMBER", "1234567801")
    monkeypatch.setenv("PAPER_WATCHLIST", "KR:005930")
    monkeypatch.setenv("KIS_ENVIRONMENT", "paper")
    monkeypatch.setenv("KIS_MARKET_DATA_ENABLED", "false")
    monkeypatch.setattr("app.paper_runtime.snapshot_store.enabled", lambda: True)
    for name in (
        "LIVE_TRADING_ENABLED", "KIS_REAL_ORDERS_ENABLED", "KIS_ACCOUNT_READ_ENABLED",
        "KIS_REAL_ACCOUNT_READ_ENABLED", "REAL_PROTECTIVE_EXIT_MANAGER_ENABLED",
        "KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED", "KIS_REAL_RECONCILIATION_ENABLED",
        "REAL_SIGNAL_SUBMISSION_ENABLED",
    ):
        monkeypatch.setenv(name, "true")
    runtime = PaperSessionFeedMonitor()

    async def hold_feed(_watches, _settings):
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_run", hold_feed)

    async def scenario():
        await runtime.start()
        status = runtime.status()
        await runtime.stop()
        return status

    status = asyncio.run(scenario())

    assert status["real_signal_state"] == "LOCKED_KIS_REST_SETTINGS"
    assert status["orders_enabled"] is False
    assert runtime._real_signal_client is None
    assert runtime._real_signal_queue is None


def test_real_signal_worker_sends_stable_key_for_approved_closed_bar(monkeypatch) -> None:
    watch = parse_watchlist("US:AAPL:NAS")[0]
    timestamp = datetime(2026, 11, 6, 15, 0, tzinfo=ZoneInfo("America/New_York"))
    bar = MinuteBar(timestamp, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), 1000,
                    Decimal("5"))
    decision = {
        "market": "US", "symbol": "AAPL", "signal": "BUY_CANDIDATE", "strategy_id": "ORB_VWAP",
        "model_id": "model-v1", "evaluated_at": timestamp.isoformat(),
    }
    class Client:
        def __init__(self):
            self.calls = []

        def submit_signal(self, **values):
            self.calls.append(values)
            return {"status": "ACCEPTED"}

    runtime = PaperSessionFeedMonitor()
    runtime._real_monitor_mode = True
    runtime._real_signal_queue = asyncio.Queue(maxsize=2)
    runtime._real_signal_client = Client()

    async def scenario():
        task = asyncio.create_task(runtime._real_signal_loop())
        runtime._queue_real_signal(watch, bar, decision)
        for _ in range(100):
            if runtime.status()["real_signal_state"] == "SUBMITTED":
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    expected_key = real_signal_idempotency_key(
        market="US", symbol="AAPL", exchange="NAS", bar_timestamp=timestamp, model_id="model-v1",
    )
    assert len(runtime._real_signal_client.calls) == 1
    assert runtime._real_signal_client.calls[0] == {
        "idempotency_key": expected_key, "market": "US", "symbol": "AAPL", "exchange": "NAS",
    }
    assert runtime.status()["last_real_signal"]["idempotency_key"] == str(expected_key)


def test_real_signal_worker_stops_after_ambiguous_write_and_never_retries(monkeypatch) -> None:
    watch = parse_watchlist("KR:005930")[0]
    timestamp = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    bar = MinuteBar(timestamp, Decimal("70000"), Decimal("70100"), Decimal("69900"), Decimal("70000"),
                    1000, Decimal("5"))
    decision = {
        "market": "KR", "symbol": "005930", "signal": "BUY_CANDIDATE", "strategy_id": "ORB_VWAP",
        "model_id": "model-v1", "evaluated_at": timestamp.isoformat(),
    }
    class Client:
        calls = 0

        def submit_signal(self, **_values):
            self.calls += 1
            raise RealSignalOrderOutcomeUnknown("do not expose this detail")

    runtime = PaperSessionFeedMonitor()
    runtime._real_monitor_mode = True
    runtime._real_signal_queue = asyncio.Queue(maxsize=2)
    runtime._real_signal_client = Client()

    async def scenario():
        task = asyncio.create_task(runtime._real_signal_loop())
        runtime._queue_real_signal(watch, bar, decision)
        for _ in range(100):
            if runtime.status()["real_signal_state"] == "MANUAL_RECONCILIATION_REQUIRED":
                break
            await asyncio.sleep(0.01)
        runtime._queue_real_signal(watch, bar, decision)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert runtime._real_signal_client.calls == 1
    assert runtime.status()["real_signal_error"] == "SIGNAL_OUTCOME_UNKNOWN"


def test_real_signal_queue_overflow_discards_unsent_candidates_and_blocks_worker() -> None:
    watch = parse_watchlist("KR:005930")[0]
    timestamp = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    bar = MinuteBar(timestamp, Decimal("70000"), Decimal("70100"), Decimal("69900"), Decimal("70000"),
                    1000, Decimal("5"))
    decision = {
        "market": "KR", "symbol": "005930", "signal": "BUY_CANDIDATE", "strategy_id": "ORB_VWAP",
        "model_id": "model-v1", "evaluated_at": timestamp.isoformat(),
    }

    class Client:
        calls = 0

        def submit_signal(self, **_values):
            self.calls += 1
            return {"status": "ACCEPTED"}

    runtime = PaperSessionFeedMonitor()
    runtime._real_monitor_mode = True
    runtime._real_signal_queue = asyncio.Queue(maxsize=1)
    runtime._real_signal_client = Client()
    runtime._real_signal_queue.put_nowait((watch, bar, decision))
    runtime._queue_real_signal(watch, bar, decision)

    assert runtime._real_signal_blocked is True
    assert runtime._real_signal_queue.empty()

    async def scenario():
        await asyncio.wait_for(runtime._real_signal_queue.join(), timeout=0.1)
        task = asyncio.create_task(runtime._real_signal_loop())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert runtime._real_signal_client.calls == 0
    assert runtime.status()["real_signal_state"] == "BLOCKED_QUEUE_FULL"


def test_us_paper_watchlist_requires_separate_read_only_real_market_data_switch(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_SESSION_MONITOR_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("KIS_WEBSOCKET_ENABLED", "true")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "paper-key")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "paper-secret")
    monkeypatch.setenv("KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED", "false")
    monkeypatch.setenv("PAPER_WATCHLIST", "US:AAPL:NAS")
    runtime = PaperSessionFeedMonitor()

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "BLOCKED"
    assert status["enabled"] is False
    assert status["last_error"] == "REAL_US_MARKET_DATA_STREAM_DISABLED"
    assert status["orders_enabled"] is False


def test_us_paper_watchlist_requires_real_market_data_credentials(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_SESSION_MONITOR_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("KIS_WEBSOCKET_ENABLED", "true")
    monkeypatch.setenv("KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED", "true")
    monkeypatch.setenv("KIS_REAL_APP_KEY", "")
    monkeypatch.setenv("KIS_REAL_APP_SECRET", "")
    monkeypatch.setenv("PAPER_WATCHLIST", "US:AAPL:NAS")
    runtime = PaperSessionFeedMonitor()

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "BLOCKED"
    assert status["last_error"] == "REAL_US_MARKET_DATA_STREAM_NOT_CONFIGURED"
    assert status["orders_enabled"] is False


def test_mixed_paper_watchlist_uses_paper_kr_and_real_read_only_us_feeds(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_SESSION_MONITOR_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("KIS_WEBSOCKET_ENABLED", "true")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "paper-key")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "paper-secret")
    monkeypatch.setenv("KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED", "true")
    monkeypatch.setenv("KIS_REAL_APP_KEY", "real-key")
    monkeypatch.setenv("KIS_REAL_APP_SECRET", "real-secret")
    monkeypatch.setenv("PAPER_WATCHLIST", "KR:005930;US:AAPL:NAS")
    runtime = PaperSessionFeedMonitor()
    feeds = []

    async def hold_feed(watches, settings):
        feeds.append((tuple(watches), settings.kis.environment))
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_run", hold_feed)

    async def start_and_stop():
        await runtime.start()
        await asyncio.sleep(0)
        status = runtime.status()
        await runtime.stop()
        return status

    status = asyncio.run(start_and_stop())

    assert {(watches[0].market, environment) for watches, environment in feeds} == {
        ("KR", "paper"), ("US", "real"),
    }
    assert status["stream_connections"] == {"paper": "CONNECTING", "real": "CONNECTING"}
    assert status["orders_enabled"] is False


def test_paper_monitor_restores_same_session_bars_before_connecting(monkeypatch) -> None:
    from app import snapshot_store

    watch = parse_watchlist("KR:005930")[0]
    start = datetime.now(ZoneInfo("Asia/Seoul")).replace(hour=9, minute=0, second=0, microsecond=0)
    restored = [MinuteBar(
        start + timedelta(minutes=index), Decimal("100"), Decimal("100.5"), Decimal("99.5"),
        Decimal("100"), 1000, Decimal("5"),
    ) for index in range(21)]
    monkeypatch.setenv("PAPER_SESSION_MONITOR_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("KIS_WEBSOCKET_ENABLED", "true")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "paper-key")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "paper-secret")
    monkeypatch.setenv("PAPER_WATCHLIST", "KR:005930")
    monkeypatch.setattr(snapshot_store, "enabled", lambda: True)
    monkeypatch.setattr(snapshot_store, "load_session_minute_bars", lambda market, symbol, session_date: restored)
    runtime = PaperSessionFeedMonitor()

    async def hold_feed(_watches, _settings):
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "_run", hold_feed)

    async def start_and_stop():
        await runtime.start()
        loaded = runtime.bars(watch)
        status = runtime.status()
        await runtime.stop()
        return loaded, status

    loaded, status = asyncio.run(start_and_stop())

    assert loaded == tuple(restored)
    assert status["restored_bars"] == 21
    assert status["decisions"][watch.key]["bars_in_session"] == 21
    assert status["orders_enabled"] is False


def test_paper_monitor_fails_closed_when_persisted_bar_restore_fails(monkeypatch) -> None:
    from app import snapshot_store

    monkeypatch.setenv("PAPER_SESSION_MONITOR_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("KIS_WEBSOCKET_ENABLED", "true")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "paper-key")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "paper-secret")
    monkeypatch.setenv("PAPER_WATCHLIST", "KR:005930")
    monkeypatch.setattr(snapshot_store, "enabled", lambda: True)
    monkeypatch.setattr(snapshot_store, "load_session_minute_bars", lambda *_args: (_ for _ in ()).throw(RuntimeError()))
    runtime = PaperSessionFeedMonitor()

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "BLOCKED"
    assert status["last_error"] == "PAPER_BAR_HISTORY_RESTORE_FAILED"
    assert status["bar_storage_error"] == "POSTGRES_BAR_RESTORE_FAILED"
    assert status["automation_error"] == "BAR_HISTORY_RESTORE_FAILED"
    assert not runtime._stream_tasks


def test_paper_autotrading_requires_persisted_session_history(monkeypatch) -> None:
    from app import snapshot_store

    monkeypatch.setenv("PAPER_SESSION_MONITOR_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("KIS_WEBSOCKET_ENABLED", "true")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "paper-key")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "paper-secret")
    monkeypatch.setenv("PAPER_WATCHLIST", "KR:005930")
    monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "true")
    monkeypatch.setattr(snapshot_store, "enabled", lambda: False)
    runtime = PaperSessionFeedMonitor()

    asyncio.run(runtime.start())

    status = runtime.status()
    assert status["state"] == "BLOCKED"
    assert status["last_error"] == "PAPER_HISTORY_PERSISTENCE_REQUIRED"
    assert status["automation_state"] == "BLOCKED"
    assert status["automation_error"] == "PAPER_HISTORY_PERSISTENCE_REQUIRED"
    assert runtime._automation_task is None
    assert not runtime._stream_tasks


def test_closed_bars_update_non_executable_quant_decision_and_reset_each_session() -> None:
    runtime = PaperSessionFeedMonitor()
    watch = parse_watchlist("KR:005930")[0]
    runtime._bars[watch.key] = deque(maxlen=5_000)
    runtime._decisions[watch.key] = {}
    start = datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    for index in range(20):
        price = Decimal("70000")
        runtime._record_closed_bar(watch, MinuteBar(start + timedelta(minutes=index), price, price, price,
                                                   price, 1000, Decimal("5")))

    decision = runtime.decisions()[watch.key]
    assert decision["signal"] == "HOLD"
    assert decision["reason"] == "STRATEGY_FILTERS_NOT_MET"
    assert decision["bars_in_session"] == 20
    assert decision["executable"] is False

    next_session = datetime(2026, 11, 6, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    runtime._record_closed_bar(watch, MinuteBar(next_session, Decimal("70000"), Decimal("70000"),
                                               Decimal("70000"), Decimal("70000"), 1000, Decimal("5")))
    assert runtime.decisions()[watch.key]["bars_in_session"] == 1


@pytest.mark.parametrize(("timestamp", "expected_reason"), [
    ("2026-11-06T09:29:00-05:00", "OUTSIDE_REGULAR_SESSION"),
    ("2026-11-06T16:00:00-05:00", "OUTSIDE_REGULAR_SESSION"),
    ("2026-11-05T15:20:00+09:00", "OUTSIDE_REGULAR_SESSION"),
    ("2026-11-07T10:00:00-05:00", "MARKET_CLOSED"),
])
def test_strategy_history_excludes_premarket_postmarket_krx_auction_and_holidays(
    timestamp: str, expected_reason: str,
) -> None:
    runtime = PaperSessionFeedMonitor()
    watch = parse_watchlist("US:AAPL:NAS")[0] if "-05:00" in timestamp else parse_watchlist("KR:005930")[0]
    runtime._bars[watch.key] = deque([
        MinuteBar(datetime.fromisoformat("2026-11-05T09:30:00-05:00"),
                  Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), 1000, Decimal("5"))
    ])
    runtime._decisions[watch.key] = {}
    bar = MinuteBar(datetime.fromisoformat(timestamp), Decimal("100"), Decimal("101"),
                    Decimal("99"), Decimal("100"), 1000, Decimal("5"))

    runtime._record_closed_bar(watch, bar)

    decision = runtime.decisions()[watch.key]
    assert not runtime._bars[watch.key]
    assert decision["signal"] == "HOLD"
    assert decision["reason"] == expected_reason
    assert decision["bars_in_session"] == 0
    assert decision["executable"] is False


def test_regular_open_starts_strategy_history_at_first_continuous_minute() -> None:
    runtime = PaperSessionFeedMonitor()
    watch = parse_watchlist("US:AAPL:NAS")[0]
    runtime._bars[watch.key] = deque(maxlen=5_000)
    runtime._decisions[watch.key] = {}

    premarket = MinuteBar(datetime.fromisoformat("2026-11-06T09:29:00-05:00"),
                          Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), 1000, Decimal("5"))
    runtime._record_closed_bar(watch, premarket)
    start = datetime.fromisoformat("2026-11-06T09:30:00-05:00")
    for index in range(20):
        price = Decimal("100")
        runtime._record_closed_bar(watch, MinuteBar(
            start + timedelta(minutes=index), price, price, price, price, 1000, Decimal("5"),
        ))

    decision = runtime.decisions()[watch.key]
    assert decision["bars_in_session"] == 20
    assert decision["reason"] != "BAR_SEQUENCE_INVALID"
    assert runtime._bars[watch.key][0].timestamp == start


def test_us_session_history_does_not_reset_at_korean_midnight() -> None:
    runtime = PaperSessionFeedMonitor()
    watch = parse_watchlist("US:AAPL:NAS")[0]
    runtime._bars[watch.key] = deque(maxlen=5_000)
    runtime._decisions[watch.key] = {}
    start = datetime.fromisoformat("2026-11-06T23:30:00+09:00")

    for index in range(40):
        price = Decimal("100")
        runtime._record_closed_bar(watch, MinuteBar(
            start + timedelta(minutes=index), price, price, price, price, 1000, Decimal("5"),
        ))

    decision = runtime.decisions()[watch.key]
    assert start.date() != runtime._bars[watch.key][-1].timestamp.date()
    assert decision["bars_in_session"] == 40
    assert decision["reason"] != "BAR_SEQUENCE_INVALID"
    assert runtime._bars[watch.key][0].timestamp == start


def test_quant_candidate_is_held_without_an_active_approved_market_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AI_MODEL_REGISTRY_PATH", str(tmp_path / "models"))
    runtime = PaperSessionFeedMonitor()
    watch = parse_watchlist("KR:005930")[0]
    runtime._bars[watch.key] = deque(maxlen=5_000)
    runtime._decisions[watch.key] = {}
    start = datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    for index in range(21):
        price = Decimal("1002") if index == 20 else Decimal("1000")
        runtime._record_closed_bar(watch, MinuteBar(
            start + timedelta(minutes=index), price, price + Decimal("0.1"), price - Decimal("0.1"),
            price, 2000 if index == 20 else 1000, Decimal("5"),
        ))

    decision = runtime.decisions()[watch.key]
    assert decision["signal"] == "HOLD"
    assert decision["reason"] == "AI_MODEL_NOT_APPROVED_FOR_MARKET"
    assert decision["model_id"] is None
    assert decision["executable"] is False


def test_approved_ai_candidate_publishes_atr_stop_distance_for_server_risk_check(monkeypatch) -> None:
    class Registry:
        def active_approved_model(self, _market):
            return {"model_id": "lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}

        def predict_preview(self, _model_id, _bars, *, market):
            assert market == "KR"
            return {"probability_positive": 0.9, "threshold": 0.5}

    monkeypatch.setattr("app.paper_runtime.LocalModelRegistry", lambda *_args, **_kwargs: Registry())
    runtime = PaperSessionFeedMonitor()
    watch = parse_watchlist("KR:005930")[0]
    runtime._bars[watch.key] = deque(maxlen=5_000)
    runtime._decisions[watch.key] = {}
    start = datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    for index in range(21):
        price = Decimal("1002") if index == 20 else Decimal("1000")
        runtime._record_closed_bar(watch, MinuteBar(
            start + timedelta(minutes=index), price, price + Decimal("0.1"), price - Decimal("0.1"),
            price, 2000 if index == 20 else 1000, Decimal("5"),
        ))

    decision = runtime.decisions()[watch.key]
    assert decision["signal"] == "BUY_CANDIDATE"
    assert decision["strategy_id"] == "ORB_VWAP"
    assert decision["model_id"] == "lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert Decimal(decision["stop_distance_per_share"]) > 0
    assert decision["executable"] is False


def test_exit_worker_uses_persisted_stop_and_submits_only_local_paper_exit() -> None:
    class Broker:
        def get_strategy_positions(self):
            return [{"market": "KR", "symbol": "005930", "currency": "KRW", "quantity": 2,
                     "stopPrice": "98", "targetPrice": "104", "openedAt": "2026-11-05T00:00:00Z",
                     "strategyId": "ORB_VWAP", "modelId": "lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]

    class Trader:
        def __init__(self):
            self.exit = None

        def submit_exit(self, **kwargs):
            self.exit = kwargs
            return {"state": "FILLED", "executable": False}

    watch = parse_watchlist("KR:005930")[0]
    at = datetime(2026, 11, 5, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    bar = MinuteBar(at, Decimal("100"), Decimal("105"), Decimal("95"), Decimal("101"), 1000, Decimal("5"))
    trader = Trader()

    result = PaperSessionFeedMonitor._manage_strategy_exit(Broker(), trader, watch, bar, PaperOpsLimits())

    assert result["state"] == "FILLED"
    assert trader.exit["reason"] == "ATR_STOP"
    assert trader.exit["exit_price"] == Decimal("98")
    assert trader.exit["quantity"] == 2
    assert trader.exit["position_opened_at"] == "2026-11-05T00:00:00Z"
    assert trader.exit["strategy_id"] == "ORB_VWAP"
    assert trader.exit["model_id"] == "lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_daily_baseline_worker_captures_early_session_equity(monkeypatch) -> None:
    class Broker:
        recorded = None

        def get_daily_risk_baselines(self, _session_date):
            return []

        def get_positions(self):
            return []

        def get_cash_balances(self):
            return [{"currency": "KRW", "balance": "1000000"}]

        def record_daily_risk_baseline(self, session_date, currency, equity):
            self.recorded = (session_date, currency, equity)
            return {"currency": currency, "equity": str(equity)}

    now = datetime.now(ZoneInfo("UTC"))
    monkeypatch.setattr("app.paper_runtime.exchange_session_window", lambda _market, _at: (now - timedelta(seconds=30), now + timedelta(hours=6)))
    monkeypatch.setattr("app.paper_runtime.baseline_capture_open", lambda **_kwargs: True)
    watch = parse_watchlist("KR:005930")[0]
    bar = MinuteBar(now.astimezone(ZoneInfo("Asia/Seoul")), Decimal("70000"), Decimal("70100"),
                    Decimal("69900"), Decimal("70000"), 1000, Decimal("5"))
    broker = Broker()

    result = PaperSessionFeedMonitor._capture_daily_baseline(
        broker, watch, bar, {}, PaperOpsLimits()
    )

    assert result["state"] == "BASELINE_CAPTURED"
    assert broker.recorded == (bar.timestamp.date(), "KRW", Decimal("1000000"))


def test_daily_baseline_worker_blocks_when_any_held_position_lacks_a_fresh_mark(monkeypatch) -> None:
    class Broker:
        def get_daily_risk_baselines(self, _session_date):
            return []

        def get_positions(self):
            return [{"currency": "KRW", "market": "KR", "symbol": "000660", "quantity": 1}]

        def get_cash_balances(self):
            return [{"currency": "KRW", "balance": "1000000"}]

        def record_daily_risk_baseline(self, *_args):
            raise AssertionError("must not persist an incomplete valuation")

    now = datetime.now(ZoneInfo("UTC"))
    monkeypatch.setattr("app.paper_runtime.exchange_session_window", lambda _market, _at: (now - timedelta(seconds=30), now + timedelta(hours=6)))
    monkeypatch.setattr("app.paper_runtime.baseline_capture_open", lambda **_kwargs: True)
    watch = parse_watchlist("KR:005930")[0]
    bar = MinuteBar(now.astimezone(ZoneInfo("Asia/Seoul")), Decimal("70000"), Decimal("70100"),
                    Decimal("69900"), Decimal("70000"), 1000, Decimal("5"))

    result = PaperSessionFeedMonitor._capture_daily_baseline(
        Broker(), watch, bar, {}, PaperOpsLimits()
    )

    assert result["state"] == "BLOCKED"
    assert result["reason"] == "POSITION_QUOTE_MISSING"


def test_daily_baseline_worker_reports_existing_value_without_rewriting(monkeypatch) -> None:
    class Broker:
        def get_daily_risk_baselines(self, session_date):
            return [{"currency": "KRW", "equity": "1000000", "sessionDate": session_date.isoformat()}]

        def get_positions(self):
            raise AssertionError("existing baseline requires no valuation")

        def get_cash_balances(self):
            raise AssertionError("existing baseline must remain immutable")

        def record_daily_risk_baseline(self, *_args):
            raise AssertionError("existing baseline must never be rewritten")

    now = datetime.now(ZoneInfo("UTC"))
    watch = parse_watchlist("KR:005930")[0]
    bar = MinuteBar(now.astimezone(ZoneInfo("Asia/Seoul")), Decimal("70000"), Decimal("70100"),
                    Decimal("69900"), Decimal("70000"), 1000, Decimal("5"))

    result = PaperSessionFeedMonitor._capture_daily_baseline(
        Broker(), watch, bar, {}, PaperOpsLimits()
    )

    assert result["state"] == "BASELINE_PRESENT"
    assert result["session_date"] == bar.timestamp.date().isoformat()


class _EntryBroker:
    def __init__(self, *, cash: str = "1000000", baseline: str = "1000000") -> None:
        self.cash = cash
        self.baseline = baseline
        self.created_orders = 0
        self.recorded_fills = 0
        self.last_fill = None
        self.last_order = None

    def get_positions(self):
        return []

    def get_cash_balances(self):
        return [{"currency": "KRW", "balance": self.cash}]

    def get_daily_risk_baselines(self, _session_date):
        return [{"currency": "KRW", "equity": self.baseline}]

    def get_operations_summary(self):
        return {"consecutiveLosingTrades": 0, "openOrders": 0}

    def get_order_by_idempotency_key(self, _key):
        return None

    def submit_order(self, *, idempotency_key, market, symbol, side, quantity, limit_price, currency,
                     strategy_id=None, model_id=None):
        self.created_orders += 1
        self.last_order = {"id": 8, "market": market, "symbol": symbol, "side": side, "quantity": quantity,
                           "limitPrice": str(limit_price), "currency": currency, "strategyId": strategy_id,
                           "modelId": model_id, "status": "PENDING", "filledQuantity": 0}
        return self.last_order

    def record_fill(self, order_id, *, fill_key, quantity, fill_price, stop_price=None, target_price=None):
        self.recorded_fills += 1
        self.last_fill = {"quantity": quantity, "fill_price": fill_price,
                          "stop_price": stop_price, "target_price": target_price}
        return {"id": order_id, "status": "FILLED", "filledQuantity": quantity}

    def close(self):
        pass


def _prepare_entry_runtime(monkeypatch, *, cash: str = "1000000", baseline: str = "1000000"):
    monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("KIS_PAPER_ORDERS_ENABLED", "false")
    monkeypatch.setattr(
        "app.paper_ops.exchange_session_window",
        lambda _market, _at: (datetime.now(ZoneInfo("UTC")) - timedelta(hours=1),
                              datetime.now(ZoneInfo("UTC")) + timedelta(hours=4)),
    )
    runtime = PaperSessionFeedMonitor()
    runtime._set_status(state="RUNNING", enabled=True)
    watch = parse_watchlist("KR:005930")[0]
    runtime._bars[watch.key] = deque(maxlen=5_000)
    now = datetime.now(ZoneInfo("UTC"))
    start = now.astimezone(ZoneInfo("Asia/Seoul")) - timedelta(minutes=20)
    for index in range(20):
        price = Decimal("100")
        runtime._bars[watch.key].append(MinuteBar(start + timedelta(minutes=index), price,
                                                 price + Decimal("1"), price - Decimal("1"),
                                                 price, 1000, Decimal("5")))
    bar = MinuteBar(now.astimezone(ZoneInfo("Asia/Seoul")), Decimal("100"), Decimal("101"),
                    Decimal("99"), Decimal("100"), 1000, Decimal("5"))
    quote_at = datetime.now(ZoneInfo("UTC"))
    quotes = {("KR", "005930"): (Decimal("100"), quote_at)}
    broker = _EntryBroker(cash=cash, baseline=baseline)
    return runtime, watch, bar, quotes, broker


def test_approved_candidate_enters_only_local_paper_ledger_after_risk_gates(monkeypatch) -> None:
    runtime, watch, bar, quotes, broker = _prepare_entry_runtime(monkeypatch)
    trader = PaperAutoTrader(broker)

    result = runtime._manage_strategy_entry(
        broker, trader, watch, bar, {"signal": "BUY_CANDIDATE", "model_id": "approved-kr-model"},
        quotes, PaperOpsLimits(),
    )

    assert result["state"] == "FILLED"
    assert broker.created_orders == 1
    assert broker.recorded_fills == 1
    assert Decimal(0) < broker.last_fill["stop_price"] < bar.close < broker.last_fill["target_price"]
    assert broker.last_order["strategyId"] == "ORB_VWAP"
    assert broker.last_order["modelId"] == "approved-kr-model"


def test_stale_signal_bar_blocks_entry_before_reading_or_writing_broker(monkeypatch) -> None:
    runtime, watch, bar, quotes, broker = _prepare_entry_runtime(monkeypatch)
    stale_bar = MinuteBar(bar.timestamp - timedelta(minutes=5), bar.open, bar.high, bar.low,
                          bar.close, bar.volume, bar.spread_bps)
    trader = PaperAutoTrader(broker)

    result = runtime._manage_strategy_entry(
        broker, trader, watch, stale_bar, {"signal": "BUY_CANDIDATE", "model_id": "approved-kr-model"},
        quotes, PaperOpsLimits(),
    )

    assert result == {"state": "BLOCKED", "reason": "SIGNAL_BAR_STALE", "executable": False}
    assert broker.created_orders == 0
    assert broker.recorded_fills == 0


def test_daily_loss_limit_blocks_candidate_before_any_paper_order_write(monkeypatch) -> None:
    runtime, watch, bar, quotes, broker = _prepare_entry_runtime(
        monkeypatch, cash="989000", baseline="1000000"
    )
    trader = PaperAutoTrader(broker)

    result = runtime._manage_strategy_entry(
        broker, trader, watch, bar, {"signal": "BUY_CANDIDATE", "model_id": "approved-kr-model"},
        quotes, PaperOpsLimits(),
    )

    assert result["state"] == "BLOCKED"
    assert "DAILY_LOSS_LIMIT" in result["reason"]
    assert broker.created_orders == 0
    assert broker.recorded_fills == 0
