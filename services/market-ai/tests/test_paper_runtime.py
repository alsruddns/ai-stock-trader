import asyncio
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.paper_runtime import PaperSessionFeedMonitor, parse_watchlist
from app.paper_ops import PaperOpsLimits
from app.quant import MinuteBar


def test_watchlist_normalizes_and_deduplicates_valid_symbols() -> None:
    watches = parse_watchlist("KR:005930;US:aapl:nas;KR:005930")
    assert [watch.key for watch in watches] == ["KR:005930:", "US:AAPL:NAS"]


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


def test_live_mode_blocks_paper_session_monitor(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_SESSION_MONITOR_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    runtime = PaperSessionFeedMonitor()

    asyncio.run(runtime.start())

    assert runtime.status()["state"] == "BLOCKED"
    assert runtime.status()["last_error"] == "PAPER_MODE_REQUIRED"


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


def test_quant_candidate_is_held_without_an_active_approved_market_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AI_MODEL_REGISTRY_PATH", str(tmp_path / "models"))
    runtime = PaperSessionFeedMonitor()
    watch = parse_watchlist("KR:005930")[0]
    runtime._bars[watch.key] = deque(maxlen=5_000)
    runtime._decisions[watch.key] = {}
    start = datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    for index in range(20):
        price = Decimal("1002") if index == 19 else Decimal("1000")
        runtime._record_closed_bar(watch, MinuteBar(
            start + timedelta(minutes=index), price, price + Decimal("0.1"), price - Decimal("0.1"),
            price, 2000 if index == 19 else 1000, Decimal("5"),
        ))

    decision = runtime.decisions()[watch.key]
    assert decision["signal"] == "HOLD"
    assert decision["reason"] == "AI_MODEL_NOT_APPROVED_FOR_MARKET"
    assert decision["model_id"] is None
    assert decision["executable"] is False


def test_exit_worker_uses_persisted_stop_and_submits_only_local_paper_exit() -> None:
    class Broker:
        def get_strategy_positions(self):
            return [{"market": "KR", "symbol": "005930", "currency": "KRW", "quantity": 2,
                     "stopPrice": "98", "targetPrice": "104", "openedAt": "2026-11-05T00:00:00Z"}]

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
