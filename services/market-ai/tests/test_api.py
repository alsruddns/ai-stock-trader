from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest
from app import main
from app.main import app, get_kis_client, get_kis_real_market_data_client, get_market_data_client
from app.kis import (HistoricalMinuteSession, KisApiError, KisRateLimitError,
                     KisSharedRateLimitUnavailable, MarketCandidate, Quote)
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from app.quant import MinuteBar

client = TestClient(app)


def test_health_discloses_read_only_mode() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["broker_connected"] is False
    assert response.json()["mode"] == "read-only-preview"
    assert response.json()["paper_automation_enabled"] is False
    assert response.json()["live_trading_enabled"] is False
    assert response.json()["paper_session_monitor"] == "DISABLED"


def test_health_lists_missing_cost_assumption_names_without_values(monkeypatch) -> None:
    names = [
        f"BACKTEST_{market}_{suffix}"
        for market in ("KR", "US")
        for suffix in (
            "BUY_COMMISSION_BPS", "SELL_COMMISSION_BPS", "SELL_TAX_BPS",
            "SLIPPAGE_BPS", "ROUND_TRIP_COST_BPS",
        )
    ]
    for name in names:
        monkeypatch.delenv(name, raising=False)

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    readiness = response.json()["strategy_cost_assumptions"]
    for market in ("KR", "US"):
        assert readiness[market] == {
            "configured": False,
            "missing": [f"BACKTEST_{market}_{suffix}" for suffix in (
                "BUY_COMMISSION_BPS", "SELL_COMMISSION_BPS", "SELL_TAX_BPS",
                "SLIPPAGE_BPS", "ROUND_TRIP_COST_BPS",
            )],
            "invalid": [],
        }
    assert all(set(value) == {"configured", "missing", "invalid"} for value in readiness.values())


def test_health_reports_cost_assumptions_ready_only_when_cost_floor_is_valid(monkeypatch) -> None:
    values = {
        "BUY_COMMISSION_BPS": "2",
        "SELL_COMMISSION_BPS": "3",
        "SELL_TAX_BPS": "4",
        "SLIPPAGE_BPS": "5",
        "ROUND_TRIP_COST_BPS": "19",
    }
    for market in ("KR", "US"):
        for suffix, value in values.items():
            monkeypatch.setenv(f"BACKTEST_{market}_{suffix}", value)

    ready = client.get("/api/v1/health").json()["strategy_cost_assumptions"]
    assert ready == {
        "KR": {"configured": True, "missing": [], "invalid": []},
        "US": {"configured": True, "missing": [], "invalid": []},
    }

    monkeypatch.setenv("BACKTEST_KR_ROUND_TRIP_COST_BPS", "18")
    rejected = client.get("/api/v1/health").json()["strategy_cost_assumptions"]
    assert rejected["KR"] == {
        "configured": False,
        "missing": [],
        "invalid": ["BACKTEST_KR_ROUND_TRIP_COST_BPS"],
    }


def test_health_reports_connected_only_when_all_kis_feeds_are_running(monkeypatch) -> None:
    monkeypatch.setattr(main.paper_session_monitor, "status", lambda: {
        "state": "RUNNING", "enabled": True,
        "stream_connections": {"paper": "RUNNING", "real": "RUNNING"},
    })

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["broker_connected"] is True


def test_health_does_not_report_connected_when_one_kis_feed_is_reconnecting(monkeypatch) -> None:
    monkeypatch.setattr(main.paper_session_monitor, "status", lambda: {
        "state": "PARTIAL", "enabled": True,
        "stream_connections": {"paper": "RUNNING", "real": "RECONNECTING"},
    })

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["broker_connected"] is False


def test_health_reports_korean_calendar_compatibility(monkeypatch) -> None:
    import pandas as pd

    monkeypatch.setattr(pd, "__version__", "3.1.1")
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["kr_calendar_compatibility"] == "PANDAS_UNSUPPORTED"


def test_health_rejects_old_exchange_calendar_dependency(monkeypatch) -> None:
    import exchange_calendars as calendars
    import pandas as pd

    monkeypatch.setattr(calendars, "__version__", "4.13.0")
    monkeypatch.setattr(pd, "__version__", "3.0.6")
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["kr_calendar_compatibility"] == "EXCHANGE_CALENDAR_UNSUPPORTED"


def test_paper_session_status_is_monitor_only() -> None:
    response = client.get("/api/v1/paper/session")
    assert response.status_code == 200
    assert response.json()["orders_enabled"] is False
    assert response.json()["kr_calendar_compatibility"] in {
        "COMPATIBLE", "PANDAS_UNSUPPORTED", "EXCHANGE_CALENDAR_UNSUPPORTED", "DEPENDENCIES_MISSING", "VERSION_UNKNOWN",
    }


def test_market_session_endpoint_reports_calendar_verified_open_window(monkeypatch) -> None:
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    window = (now - timedelta(minutes=30), now + timedelta(hours=1))
    continuous = (window[0], window[1] - timedelta(minutes=10))
    monkeypatch.setattr(main, "exchange_session_window", lambda _market, _now: window)
    monkeypatch.setattr(main, "exchange_continuous_session_window", lambda _market, _now: continuous)
    response = client.get("/api/v1/market/sessions/KR")
    assert response.status_code == 200
    body = response.json()
    assert body["market"] == "KR"
    assert body["is_session"] is True
    assert body["is_open"] is True
    assert body["open_at"] is not None
    assert body["close_at"] is not None
    assert body["continuous_open_at"] == continuous[0].isoformat()
    assert body["continuous_close_at"] == continuous[1].isoformat()
    assert body["evaluated_at"].endswith("+00:00")


def test_market_session_endpoint_reports_exchange_holiday(monkeypatch) -> None:
    monkeypatch.setattr(main, "exchange_session_window", lambda _market, _now: None)
    monkeypatch.setattr(main, "exchange_continuous_session_window", lambda _market, _now: None)
    response = client.get("/api/v1/market/sessions/US")
    assert response.status_code == 200
    assert response.json()["is_session"] is False
    assert response.json()["is_open"] is False
    assert response.json()["open_at"] is None
    assert response.json()["close_at"] is None
    assert response.json()["continuous_open_at"] is None
    assert response.json()["continuous_close_at"] is None


def test_market_session_endpoint_fails_closed_when_calendar_unavailable(monkeypatch) -> None:
    def unavailable(_market, _now):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(main, "exchange_session_window", unavailable)
    monkeypatch.setattr(main, "exchange_continuous_session_window", unavailable)
    response = client.get("/api/v1/market/sessions/KR")
    assert response.status_code == 503
    assert response.json()["detail"] == "Market calendar could not verify this session"


def test_latest_closed_session_endpoint_returns_last_verified_exchange_session(monkeypatch) -> None:
    from datetime import timedelta

    session_day = date(2026, 11, 6)
    opened = datetime(2026, 11, 6, 14, 30, tzinfo=timezone.utc)
    closed = opened + timedelta(hours=6, minutes=30)
    observed = {}
    monkeypatch.setattr(main, "latest_closed_session_date", lambda market, now: session_day)

    def session_window(market, at):
        observed["market"] = market
        observed["date"] = at.date().isoformat()
        return opened, closed

    monkeypatch.setattr(main, "exchange_session_window", session_window)
    continuous = (opened, closed - timedelta(minutes=10))
    monkeypatch.setattr(main, "exchange_continuous_session_window", lambda _market, _at: continuous)
    response = client.get("/api/v1/market/sessions/US/latest-closed")

    assert response.status_code == 200
    assert observed == {"market": "US", "date": session_day.isoformat()}
    body = response.json()
    assert body["market"] == "US"
    assert body["session_date"] == session_day.isoformat()
    assert body["is_session"] is True
    assert body["is_open"] is False
    assert body["open_at"] == opened.isoformat()
    assert body["close_at"] == closed.isoformat()
    assert body["continuous_open_at"] == continuous[0].isoformat()
    assert body["continuous_close_at"] == continuous[1].isoformat()
    assert datetime.fromisoformat(body["evaluated_at"]).tzinfo is not None


def test_latest_closed_session_endpoint_fails_closed_without_verified_session(monkeypatch) -> None:
    def unavailable(_market, _now):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(main, "latest_closed_session_date", unavailable)
    response = client.get("/api/v1/market/sessions/KR/latest-closed")

    assert response.status_code == 503
    assert response.json()["detail"] == "Latest closed market session could not be verified"


def test_market_session_endpoint_rejects_unknown_market() -> None:
    response = client.get("/api/v1/market/sessions/EU")
    assert response.status_code == 422


def test_session_quality_endpoint_audits_persisted_bars_read_only(monkeypatch) -> None:
    zone = ZoneInfo("America/New_York")
    opened = datetime(2026, 11, 5, 9, 30, tzinfo=zone)
    bars = [MinuteBar(
        timestamp=opened + timedelta(minutes=index), open=Decimal("100"), high=Decimal("101"),
        low=Decimal("99"), close=Decimal("100"), volume=1000, spread_bps=Decimal("5"),
    ) for index in range(390)]
    monkeypatch.setattr(main.snapshot_store, "enabled", lambda: True)
    monkeypatch.setattr(main.snapshot_store, "load_session_minute_bars",
                        lambda market, symbol, session_date: bars)
    monkeypatch.setattr(main.snapshot_store, "load_session_minute_bar_source_counts",
                        lambda market, symbols, session_date: {"AAPL": {"KIS_REAL_US_WEBSOCKET": 390}})

    response = client.get(
        "/api/v1/market/session-quality/US/AAPL?session_date=2026-11-05",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETE"
    assert body["observed_bars"] == 390
    assert body["spread_coverage_percent"] == 100
    assert body["source_counts"] == {"KIS_REAL_US_WEBSOCKET": 390}


def test_session_quality_endpoint_exposes_missing_bar_and_spread_timestamps(monkeypatch) -> None:
    zone = ZoneInfo("America/New_York")
    opened = datetime(2026, 11, 6, 9, 30, tzinfo=zone)
    bars = []
    for index in range(390):
        if index == 1:
            continue
        bars.append(MinuteBar(
            timestamp=opened + timedelta(minutes=index), open=Decimal("100"), high=Decimal("101"),
            low=Decimal("99"), close=Decimal("100"), volume=1000,
            spread_bps=None if index == 2 else Decimal("5"),
        ))
    monkeypatch.setattr(main.snapshot_store, "enabled", lambda: True)
    monkeypatch.setattr(main.snapshot_store, "load_session_minute_bars",
                        lambda market, symbol, session_date: bars)
    monkeypatch.setattr(main.snapshot_store, "load_session_minute_bar_source_counts",
                        lambda market, symbols, session_date: {"AAPL": {"KIS_REAL_US_WEBSOCKET": 389}})

    response = client.get("/api/v1/market/session-quality/US/AAPL?session_date=2026-11-06")

    assert response.status_code == 200
    body = response.json()
    assert body["missing_bar_timestamps"] == ["2026-11-06T14:31:00+00:00"]
    assert body["bars_without_spread_timestamps"] == ["2026-11-06T14:32:00+00:00"]


def test_session_quality_endpoint_fails_closed_when_persistence_disabled(monkeypatch) -> None:
    monkeypatch.setattr(main.snapshot_store, "enabled", lambda: False)
    response = client.get(
        "/api/v1/market/session-quality/KR/005930?session_date=2026-11-06",
    )
    assert response.status_code == 503


def test_session_quality_endpoint_does_not_query_database_for_non_session(monkeypatch) -> None:
    monkeypatch.setattr(main.snapshot_store, "load_session_minute_bars",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("database must not be queried")))
    response = client.get(
        "/api/v1/market/session-quality/US/AAPL?session_date=2026-11-07",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "NON_SESSION"


def test_session_quality_endpoint_rejects_symbol_from_wrong_market() -> None:
    response = client.get(
        "/api/v1/market/session-quality/KR/AAPL?session_date=2026-11-06",
    )
    assert response.status_code == 422


def test_session_quality_endpoint_fails_closed_when_exchange_calendar_is_unavailable(monkeypatch) -> None:
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(main, "audit_minute_bar_session", unavailable)
    response = client.get(
        "/api/v1/market/session-quality/US/AAPL?session_date=2026-11-06",
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Exchange calendar is unavailable"


def test_session_quality_batch_endpoint_audits_all_symbols_with_one_store_call(monkeypatch) -> None:
    bars = [MinuteBar(
        timestamp=datetime(2026, 11, 5, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")) + timedelta(minutes=index),
        open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100"),
        volume=1000, spread_bps=Decimal("5"),
    ) for index in range(380)]
    monkeypatch.setattr(main.snapshot_store, "enabled", lambda: True)
    calls = []

    def load(market, symbols, session_date):
        calls.append((market, symbols, session_date))
        return {"005930": bars, "069500": bars[:379]}

    monkeypatch.setattr(main.snapshot_store, "load_session_minute_bars_for_symbols", load)
    monkeypatch.setattr(main.snapshot_store, "load_session_minute_bar_source_counts",
                        lambda market, symbols, session_date: {
                            "005930": {"KIS_PAPER_WEBSOCKET": 380},
                            "069500": {"KIS_REST_MINUTE_CHART": 379},
                        })
    response = client.get(
        "/api/v1/market/session-quality/KR?symbols=005930&symbols=069500&session_date=2026-11-05",
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert [item["symbol"] for item in response.json()["sessions"]] == ["005930", "069500"]
    assert [item["status"] for item in response.json()["sessions"]] == ["COMPLETE", "INCOMPLETE"]
    assert response.json()["sessions"][0]["source_counts"] == {"KIS_PAPER_WEBSOCKET": 380}


def test_session_quality_batch_can_select_latest_closed_session(monkeypatch) -> None:
    chosen = date(2026, 11, 6)
    calls = []
    monkeypatch.setattr(main, "latest_closed_session_date", lambda market, now: chosen)
    monkeypatch.setattr(main.snapshot_store, "enabled", lambda: True)

    def load(market, symbols, session_date):
        calls.append((market, symbols, session_date))
        return {"005930": []}

    monkeypatch.setattr(main.snapshot_store, "load_session_minute_bars_for_symbols", load)
    monkeypatch.setattr(main.snapshot_store, "load_session_minute_bar_source_counts",
                        lambda market, symbols, session_date: {"005930": {"KIS_PAPER_WEBSOCKET": 0}})
    response = client.get(
        "/api/v1/market/session-quality/KR?symbols=005930&latest_closed=true",
    )

    assert response.status_code == 200
    assert response.json()["session_date"] == "2026-11-06"
    assert response.json()["sessions"][0]["status"] == "INCOMPLETE"
    assert calls == [("KR", ["005930"], chosen)]


def test_session_quality_batch_requires_one_date_selection_mode() -> None:
    missing = client.get("/api/v1/market/session-quality/US?symbols=AAPL")
    conflicting = client.get(
        "/api/v1/market/session-quality/US?symbols=AAPL&session_date=2026-11-06&latest_closed=true",
    )

    assert missing.status_code == 422
    assert conflicting.status_code == 422


def test_training_readiness_counts_only_complete_approved_websocket_sessions(monkeypatch) -> None:
    from datetime import time as datetime_time

    zone = ZoneInfo("Asia/Seoul")
    history = {}
    for session_date, source in (
        (date(2026, 11, 5), "KIS_PAPER_WEBSOCKET"),
        (date(2026, 11, 6), "KIS_REST_MINUTE_SESSION"),
    ):
        opened = datetime.combine(session_date, datetime_time(9, 0), tzinfo=zone)
        bars = [MinuteBar(
            timestamp=opened + timedelta(minutes=index),
            open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100"),
            volume=1000, spread_bps=Decimal("5"),
        ) for index in range(380)]
        history[session_date] = {
            "bars": bars,
            "source_counts": {source: len(bars)},
        }
    monkeypatch.setattr(main.snapshot_store, "enabled", lambda: True)
    calls = []

    def load(market, symbol, from_date, to_date):
        calls.append((market, symbol, from_date, to_date))
        return history

    monkeypatch.setattr(main.snapshot_store, "load_minute_bar_training_history", load)
    response = client.get(
        "/api/v1/market/training-readiness/KR/005930"
        "?from_date=2026-11-05&to_date=2026-11-06",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["minimum_required_sessions"] == 13
    assert body["eligible_sessions"] == 1
    assert body["additional_sessions_needed"] == 12
    assert body["minimum_met"] is False
    assert [row["eligible_for_training"] for row in body["sessions"]] == [True, False]
    assert calls == [("KR", "005930", date(2026, 11, 5), date(2026, 11, 6))]


def test_training_readiness_rejects_oversized_ranges_and_wrong_symbols(monkeypatch) -> None:
    monkeypatch.setattr(main.snapshot_store, "enabled", lambda: True)
    oversized = client.get(
        "/api/v1/market/training-readiness/KR/005930"
        "?from_date=2026-01-01&to_date=2026-05-02",
    )
    wrong_symbol = client.get(
        "/api/v1/market/training-readiness/KR/AAPL?from_date=2026-11-05&to_date=2026-11-06",
    )
    assert oversized.status_code == 422
    assert wrong_symbol.status_code == 422


def test_training_readiness_fails_closed_when_snapshot_persistence_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(main.snapshot_store, "enabled", lambda: False)
    response = client.get(
        "/api/v1/market/training-readiness/US/AAPL"
        "?from_date=2026-11-05&to_date=2026-11-06",
    )
    assert response.status_code == 503


def test_session_quality_batch_endpoint_limits_symbols_and_rejects_mixed_market_codes() -> None:
    too_many = client.get(
        "/api/v1/market/session-quality/US?" + "&".join(
            f"symbols=SYM{index:02d}" for index in range(51)
        ) + "&session_date=2026-11-05",
    )
    wrong_market = client.get(
        "/api/v1/market/session-quality/US?symbols=005930&session_date=2026-11-05",
    )
    assert too_many.status_code == 422
    assert wrong_market.status_code == 422


def test_model_governance_endpoint_is_read_only_for_execution(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_MODEL_REGISTRY_PATH", str(tmp_path))
    response = client.get("/api/v1/ai/models")
    assert response.status_code == 200
    assert response.json() == {
        "models": [], "governance_events": [],
        "governance_audit": {"integrity_verified": True, "event_count": 0},
        "executable": False,
    }


def test_model_governance_endpoint_reports_tampered_audit_chain(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_MODEL_REGISTRY_PATH", str(tmp_path))
    (tmp_path / "governance-audit.jsonl").write_text('{"action":"approve"}\n', encoding="utf-8")
    response = client.get("/api/v1/ai/models")
    assert response.status_code == 200
    assert response.json()["governance_audit"] == {"integrity_verified": False, "event_count": 0}
    assert response.json()["governance_events"] == []


def test_model_approval_route_is_locked_without_local_governance_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_MODEL_REGISTRY_PATH", str(tmp_path))
    response = client.post("/api/v1/ai/models/lightgbm-" + "a" * 32 + "/approve", json={
        "approved_by": "local-operator", "note": "Reviewed held-out test evidence for advisory use only.",
    })
    assert response.status_code == 503


def test_model_approval_route_requires_matching_local_governance_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_MODEL_REGISTRY_PATH", str(tmp_path))
    monkeypatch.setenv("MODEL_GOVERNANCE_TOKEN", "g" * 40)
    response = client.post("/api/v1/ai/models/lightgbm-" + "a" * 32 + "/approve", headers={
        "X-Model-Governance-Token": "wrong-token",
    }, json={"approved_by": "local-operator", "note": "Reviewed held-out test evidence for advisory use only."})
    assert response.status_code == 403


def test_model_approval_route_reaches_registry_with_local_governance_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AI_MODEL_REGISTRY_PATH", str(tmp_path))
    monkeypatch.setenv("MODEL_GOVERNANCE_TOKEN", "g" * 40)
    response = client.post("/api/v1/ai/models/lightgbm-" + "a" * 32 + "/approve", headers={
        "X-Model-Governance-Token": "g" * 40,
    }, json={"approved_by": "local-operator", "note": "Reviewed held-out test evidence for advisory use only."})
    assert response.status_code == 404


def test_live_mode_never_enables_python_paper_automation(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "live")
    response = client.get("/api/v1/health")
    assert response.json()["paper_automation_enabled"] is False
    monkeypatch.delenv("PAPER_AUTOTRADING_ENABLED", raising=False)
    monkeypatch.delenv("TRADING_MODE", raising=False)


def test_health_reports_paper_automation_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("KIS_PAPER_ORDERS_ENABLED", "false")
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["paper_automation_enabled"] is True

    monkeypatch.setenv("KIS_PAPER_ORDERS_ENABLED", "true")
    response = client.get("/api/v1/health")
    assert response.json()["paper_automation_enabled"] is False


def test_analysis_preview_cannot_be_executed() -> None:
    response = client.post("/api/v1/analysis/momentum", json={
        "market": "KR", "symbol": "005930", "bars": [{"close": value} for value in [100, 100, 100, 100, 100, 110]]
    })
    assert response.status_code == 200
    body = response.json()
    assert body["signal"] == "BUY"
    assert body["executable"] is False
    assert body["confidence"] == 0.0


def test_analysis_rejects_invalid_market() -> None:
    response = client.post("/api/v1/analysis/momentum", json={
        "market": "EU", "symbol": "XYZ", "bars": [{"close": 1}, {"close": 2}]
    })
    assert response.status_code == 422


def test_kis_quote_route_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setenv("KIS_MARKET_DATA_ENABLED", "false")
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    monkeypatch.setattr(main, "_KIS_CLIENTS", {})
    response = client.get("/api/v1/market/quotes/KR/005930")
    assert response.status_code == 503
    assert response.json()["detail"] == "KIS market data is disabled"


def test_quote_route_returns_normalized_snapshot() -> None:
    class FakeKisClient:
        def get_quote(self, market, symbol, exchange=None):
            return Quote(
                market=market,
                symbol=symbol,
                exchange=exchange,
                currency="USD" if market == "US" else "KRW",
                price=Decimal("231.59"),
                change=Decimal("3.57"),
                change_percent=Decimal("1.57"),
                volume=1000,
                retrieved_at=datetime.now(timezone.utc),
            )

    app.dependency_overrides[get_market_data_client] = lambda: FakeKisClient()
    try:
        response = client.get("/api/v1/market/quotes/US/AAPL?exchange=NAS")
        assert response.status_code == 200
        assert response.json()["price"] == "231.59"
        assert response.json()["currency"] == "USD"
        assert response.json()["source"] == "KIS_REST_SNAPSHOT"
    finally:
        app.dependency_overrides.clear()


def test_quote_route_exposes_safe_response_shape_diagnostics_only() -> None:
    class RejectedKisClient:
        def get_quote(self, *_args, **_kwargs):
            raise KisApiError(
                "KIS rejected the quote request (HTTP 200, code unknown, "
                "content-type application/json, response-bytes 104, "
                "fields rt_cd,msg_cd,msg1)",
                http_status=200,
                broker_code="unknown",
                response_content_type="application/json",
                response_bytes=104,
                response_fields=("rt_cd", "msg_cd", "msg1"),
            )

    app.dependency_overrides[get_market_data_client] = lambda: RejectedKisClient()
    try:
        response = client.get("/api/v1/market/quotes/US/F?exchange=NAS")
        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "HTTP 200, code unknown" in detail
        assert "fields rt_cd,msg_cd,msg1" in detail
        assert "application/json" in detail
        assert "response-bytes 104" in detail
        assert "sensitive" not in detail
    finally:
        app.dependency_overrides.clear()


def test_overseas_minute_session_route_uses_real_read_only_client() -> None:
    class FakeKisClient:
        def get_overseas_minute_session_snapshot(self, symbol, exchange, session_date):
            assert symbol == "AAPL"
            assert exchange == "NAS"
            assert session_date == date(2026, 11, 5)
            return HistoricalMinuteSession(
                bars=[MinuteBar(
                    timestamp=datetime.fromisoformat("2026-11-05T09:30:00-05:00"), open=Decimal("100"),
                    high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), volume=1000,
                )],
                pages_fetched=2,
                source_record_count=240,
                first_source_timestamp=datetime.fromisoformat("2026-11-05T09:30:00-05:00"),
                last_source_timestamp=datetime.fromisoformat("2026-11-06T09:30:00-05:00"),
                continuation_states=["M", "D"],
            )

    app.dependency_overrides[get_kis_real_market_data_client] = lambda: FakeKisClient()
    try:
        response = client.get(
            "/api/v1/market/sessions/US/aapl/bars?exchange=NAS&session_date=2026-11-05",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["symbol"] == "AAPL"
        assert body["session_date"] == "2026-11-05"
        assert body["source"] == "KIS_REST_MINUTE_SESSION"
        assert body["spread_bps_available"] is False
        assert body["source_pages_fetched"] == 2
        assert body["source_records_seen"] == 240
        assert body["source_first_bar_at"] == "2026-11-05T09:30:00-05:00"
        assert body["source_last_bar_at"] == "2026-11-06T09:30:00-05:00"
        assert body["source_continuation_states"] == ["M", "D"]
        assert body["bars"][0]["spread_bps"] is None
    finally:
        app.dependency_overrides.clear()


def test_us_minute_session_route_requires_explicit_real_market_data_gate(monkeypatch) -> None:
    monkeypatch.setenv("KIS_MARKET_DATA_ENABLED", "true")
    monkeypatch.setenv("KIS_REAL_MARKET_DATA_ENABLED", "false")
    monkeypatch.setenv("KIS_REAL_APP_KEY", "real-key")
    monkeypatch.setenv("KIS_REAL_APP_SECRET", "real-secret")
    response = client.get(
        "/api/v1/market/sessions/US/aapl/bars?exchange=NAS&session_date=2026-11-05",
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "KIS market data is disabled"


def test_us_quote_route_requires_explicit_real_market_data_gate(monkeypatch) -> None:
    monkeypatch.setenv("KIS_MARKET_DATA_ENABLED", "true")
    monkeypatch.setenv("KIS_REAL_MARKET_DATA_ENABLED", "false")
    monkeypatch.setenv("KIS_REAL_APP_KEY", "real-key")
    monkeypatch.setenv("KIS_REAL_APP_SECRET", "real-secret")
    response = client.get("/api/v1/market/quotes/US/AAPL?exchange=NAS")
    assert response.status_code == 503
    assert response.json()["detail"] == "KIS market data is disabled"


def test_market_candidate_route_is_read_only_and_never_returns_executable_signal(monkeypatch) -> None:
    class FakeKisClient:
        def get_ranked_candidates(self, market, *, exchange, limit, minimum_turnover, maximum_spread_bps):
            assert market == "KR"
            assert exchange is None
            assert limit == 5
            assert minimum_turnover == Decimal("1000000000")
            assert maximum_spread_bps == Decimal("15")
            return [MarketCandidate(
                market="KR", symbol="005930", name="Samsung", exchange=None, rank=1,
                currency="KRW", price=Decimal("70000"), volume=1000000,
                turnover=Decimal("200000000000"), best_ask=Decimal("70010"),
                best_bid=Decimal("70000"), spread_bps=Decimal("1.42"),
                passes_market_filter=True, filter_reason="MARKET_FILTERS_PASSED",
                executable=False, retrieved_at=datetime.now(timezone.utc),
            )]

    monkeypatch.setattr(main, "get_kis_candidate_client", lambda _market: FakeKisClient())
    monkeypatch.setattr(
        main, "exchange_continuous_session_window",
        lambda _market, now: (now - timedelta(minutes=1), now + timedelta(minutes=1)),
    )
    response = client.get(
        "/api/v1/market/candidates/KR?limit=5&minimum_turnover=1000000000",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["candidates"][0]["symbol"] == "005930"
    assert body["candidates"][0]["passes_market_filter"] is True
    assert body["candidates"][0]["freshness_verified"] is False
    assert body["candidates"][0]["executable"] is False
    assert body["executable"] is False


def test_market_candidate_route_requires_turnover_threshold_and_us_exchange(monkeypatch) -> None:
    class FakeKisClient:
        def get_ranked_candidates(self, *_args, **_kwargs):
            raise AssertionError("invalid candidate request must not call KIS")

    monkeypatch.setattr(main, "get_kis_candidate_client", lambda _market: FakeKisClient())
    monkeypatch.setattr(
        main, "exchange_continuous_session_window",
        lambda _market, now: (now - timedelta(minutes=1), now + timedelta(minutes=1)),
    )
    missing_threshold = client.get("/api/v1/market/candidates/KR")
    missing_exchange = client.get(
        "/api/v1/market/candidates/US?minimum_turnover=1000000",
    )
    assert missing_threshold.status_code == 422
    assert missing_exchange.status_code == 422


def test_market_candidate_route_rejects_after_continuous_session_before_kis_call(monkeypatch) -> None:
    class FakeKisClient:
        def get_ranked_candidates(self, *_args, **_kwargs):
            raise AssertionError("closed exchange must not call KIS")

    monkeypatch.setattr(main, "get_kis_candidate_client", lambda _market: FakeKisClient())
    monkeypatch.setattr(
        main, "exchange_continuous_session_window",
        lambda _market, now: (now - timedelta(hours=1), now - timedelta(seconds=1)),
    )
    response = client.get(
        "/api/v1/market/candidates/KR?minimum_turnover=1",
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Candidate screening requires an open continuous trading session"


def test_us_market_candidate_screen_requires_separate_real_data_gate(monkeypatch) -> None:
    from app.main import get_kis_candidate_client

    monkeypatch.setenv("KIS_MARKET_DATA_ENABLED", "true")
    monkeypatch.setenv("KIS_REAL_MARKET_DATA_ENABLED", "false")
    monkeypatch.setenv("KIS_REAL_APP_KEY", "real-key")
    monkeypatch.setenv("KIS_REAL_APP_SECRET", "real-secret")
    monkeypatch.setenv("KIS_REAL_ACCOUNT_READ_ENABLED", "false")
    with pytest.raises(HTTPException) as disabled:
        get_kis_candidate_client("US")
    assert disabled.value.status_code == 503

    captured = []
    monkeypatch.setenv("KIS_REAL_MARKET_DATA_ENABLED", "true")
    monkeypatch.setattr(main, "_get_or_create_kis_client", lambda settings: captured.append(settings) or settings)
    settings = get_kis_candidate_client("US")
    assert settings.environment == "real"
    assert settings.app_key == "real-key"
    assert settings.app_secret == "real-secret"
    assert captured == [settings]


def test_candidate_subscription_route_is_locked_by_default(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_CANDIDATE_SUBSCRIPTIONS_ENABLED", "false")
    monkeypatch.setattr(
        main, "get_kis_candidate_client",
        lambda _market: (_ for _ in ()).throw(AssertionError("disabled route must not call KIS")),
    )
    response = client.post(
        "/api/v1/market/candidates/KR/subscribe?minimum_turnover=1",
    )
    assert response.status_code == 423
    assert response.json()["detail"] == "PAPER candidate subscriptions are disabled"


def test_candidate_subscription_route_adds_only_filtered_symbols(monkeypatch) -> None:
    from app.paper_runtime import PaperWatch

    monkeypatch.setenv("PAPER_CANDIDATE_SUBSCRIPTIONS_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(main.paper_session_monitor, "status", lambda: {
        "enabled": True, "stream_connections": {"paper": "RUNNING"},
    })
    monkeypatch.setattr(
        main, "exchange_continuous_session_window",
        lambda _market, now: (now - timedelta(minutes=1), now + timedelta(minutes=1)),
    )

    class FakeKisClient:
        def get_ranked_candidates(self, market, *, exchange, limit, minimum_turnover, maximum_spread_bps):
            assert (market, exchange, limit) == ("KR", None, 5)
            assert minimum_turnover == Decimal("1000000000")
            assert maximum_spread_bps == Decimal("8")
            return [
                MarketCandidate(
                    market="KR", symbol="005930", name="Samsung", exchange=None, rank=1,
                    currency="KRW", price=Decimal("70000"), volume=1000,
                    turnover=Decimal("200000000000"), best_ask=Decimal("70010"),
                    best_bid=Decimal("70000"), spread_bps=Decimal("1.42"),
                    passes_market_filter=True, filter_reason="MARKET_FILTERS_PASSED",
                ),
                MarketCandidate(
                    market="KR", symbol="000660", name="SK Hynix", exchange=None, rank=2,
                    currency="KRW", price=Decimal("200000"), volume=100,
                    turnover=Decimal("1000000"), best_ask=None, best_bid=None, spread_bps=None,
                    passes_market_filter=False, filter_reason="LIVE_SPREAD_UNAVAILABLE",
                ),
            ]

    async def add_watches(watches, *, discovered):
        assert watches == (PaperWatch("KR", "005930"),)
        assert discovered is True
        return {"added": ["KR:005930:"], "subscriptions": 2}

    monkeypatch.setattr(main, "get_kis_candidate_client", lambda _market: FakeKisClient())
    monkeypatch.setattr(main.paper_session_monitor, "add_watches", add_watches)
    response = client.post(
        "/api/v1/market/candidates/KR/subscribe?limit=5&minimum_turnover=1000000000&maximum_spread_bps=8",
    )

    assert response.status_code == 200
    assert response.json() == {
        "market": "KR", "exchange": None, "considered": 2,
        "added": ["KR:005930:"], "subscriptions": 2, "executable": False,
    }


def test_candidate_refresh_route_blocks_while_paper_automation_is_enabled(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_CANDIDATE_SUBSCRIPTIONS_ENABLED", "true")
    monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "true")
    monkeypatch.setattr(
        main, "get_kis_candidate_client",
        lambda _market: (_ for _ in ()).throw(AssertionError("refresh must stop before KIS")),
    )
    response = client.post(
        "/api/v1/market/candidates/KR/refresh-subscriptions?minimum_turnover=1",
    )
    assert response.status_code == 423
    assert response.json()["detail"] == "PAPER automation must be disabled during candidate refresh"


def test_candidate_refresh_route_reports_position_protected_reconciliation(monkeypatch) -> None:
    from app.paper_runtime import PaperWatch

    monkeypatch.setenv("PAPER_CANDIDATE_SUBSCRIPTIONS_ENABLED", "true")
    monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "false")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(main.paper_session_monitor, "status", lambda: {
        "enabled": True, "stream_connections": {"paper": "RUNNING"},
    })
    monkeypatch.setattr(
        main, "exchange_continuous_session_window",
        lambda _market, now: (now - timedelta(minutes=1), now + timedelta(minutes=1)),
    )

    class FakeKisClient:
        def get_ranked_candidates(self, *_args, **_kwargs):
            return [MarketCandidate(
                market="KR", symbol="005930", name="Samsung", exchange=None, rank=1,
                currency="KRW", price=Decimal("70000"), volume=1000,
                turnover=Decimal("70000000"), best_ask=Decimal("70001"),
                best_bid=Decimal("70000"), spread_bps=Decimal("0.14"),
                passes_market_filter=True, filter_reason="MARKET_FILTERS_PASSED",
            )]

    async def refresh(market, desired):
        assert market == "KR"
        assert desired == (PaperWatch("KR", "005930"),)
        return {
            "added": [], "removed": [], "preserved": 1, "open_orders": 1, "subscriptions": 2,
        }

    monkeypatch.setattr(main, "get_kis_candidate_client", lambda _market: FakeKisClient())
    monkeypatch.setattr(main.paper_session_monitor, "refresh_discovered_watches", refresh)
    response = client.post(
        "/api/v1/market/candidates/KR/refresh-subscriptions?minimum_turnover=1",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["preserved"] == 1
    assert body["open_orders"] == 1
    assert body["removed"] == []
    assert body["executable"] is False


def test_kis_database_rate_limit_failure_returns_service_unavailable() -> None:
    class UnavailableKisClient:
        def get_quote(self, *_args, **_kwargs):
            raise KisSharedRateLimitUnavailable("shared KIS request pacing is unavailable")

    app.dependency_overrides[get_market_data_client] = lambda: UnavailableKisClient()
    try:
        response = client.get("/api/v1/market/quotes/KR/005930")
        assert response.status_code == 503
        assert response.json()["detail"] == "shared KIS request pacing is unavailable"
    finally:
        app.dependency_overrides.clear()


def test_kis_broker_rate_limit_returns_http_429_without_raw_response() -> None:
    class RateLimitedKisClient:
        def get_quote(self, *_args, **_kwargs):
            raise KisRateLimitError("KIS rate limit reached; wait before requesting again.")

    app.dependency_overrides[get_market_data_client] = lambda: RateLimitedKisClient()
    try:
        response = client.get("/api/v1/market/quotes/KR/005930")
        assert response.status_code == 429
        assert response.json()["detail"] == "KIS rate limit reached; wait before requesting again."
    finally:
        app.dependency_overrides.clear()


def test_domestic_minute_bars_route_exposes_missing_spread_explicitly() -> None:
    class FakeKisClient:
        def get_domestic_minute_bars(self, symbol, session_date, end_time):
            assert symbol == "005930"
            assert session_date == date(2026, 11, 5)
            assert end_time == "153000"
            return [MinuteBar(
                timestamp=datetime.fromisoformat("2026-11-05T09:00:00+09:00"), open=Decimal("100"),
                high=Decimal("101"), low=Decimal("99"), close=Decimal("100"), volume=1000,
            )]

    app.dependency_overrides[get_kis_client] = lambda: FakeKisClient()
    try:
        response = client.get("/api/v1/market/bars/KR/005930?session_date=2026-11-05")
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "KIS_REST_MINUTE_CHART"
        assert body["spread_bps_available"] is False
        assert body["bars"][0]["spread_bps"] is None
    finally:
        app.dependency_overrides.clear()


def test_domestic_full_session_bars_route_uses_distinct_source_and_never_invents_spread() -> None:
    class FakeKisClient:
        def get_domestic_minute_session_snapshot(self, symbol, session_date, end_time):
            assert symbol == "005930"
            assert session_date == date(2026, 11, 5)
            assert end_time == "153000"
            return HistoricalMinuteSession(
                bars=[MinuteBar(
                    timestamp=datetime.fromisoformat("2026-11-05T09:00:00+09:00"), open=Decimal("100"),
                    high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), volume=1000,
                )],
                pages_fetched=2,
                source_record_count=121,
                first_source_timestamp=datetime.fromisoformat("2026-11-05T09:00:00+09:00"),
                last_source_timestamp=datetime.fromisoformat("2026-11-05T15:30:00+09:00"),
                continuation_states=[],
            )

    app.dependency_overrides[get_kis_client] = lambda: FakeKisClient()
    try:
        response = client.get(
            "/api/v1/market/sessions/KR/005930/bars?session_date=2026-11-05",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "KIS_REST_MINUTE_SESSION"
        assert body["spread_bps_available"] is False
        assert body["source_pages_fetched"] == 2
        assert body["source_records_seen"] == 121
        assert body["source_first_bar_at"] == "2026-11-05T09:00:00+09:00"
        assert body["source_last_bar_at"] == "2026-11-05T15:30:00+09:00"
        assert body["source_continuation_states"] == []
        assert body["bars"][0]["spread_bps"] is None
    finally:
        app.dependency_overrides.clear()


def test_overseas_minute_bars_route_returns_latest_page_timezone_and_exchange() -> None:
    class FakeKisClient:
        def get_overseas_minute_bars(self, symbol, exchange, session_date):
            assert symbol == "AAPL"
            assert exchange == "NAS"
            assert session_date == date(2026, 11, 5)
            return [MinuteBar(
                timestamp=datetime.fromisoformat("2026-11-05T09:30:00-05:00"), open=Decimal("100"),
                high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), volume=1000,
            )]

    app.dependency_overrides[get_kis_real_market_data_client] = lambda: FakeKisClient()
    try:
        response = client.get(
            "/api/v1/market/bars/US/aapl?exchange=NAS&session_date=2026-11-05",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["symbol"] == "AAPL"
        assert body["session_date"] == "2026-11-05"
        assert body["source"] == "KIS_REST_MINUTE_CHART"
        assert body["spread_bps_available"] is False
        assert body["bars"][0]["timestamp"] == "2026-11-05T09:30:00-05:00"
        assert body["bars"][0]["spread_bps"] is None
    finally:
        app.dependency_overrides.clear()


def test_account_balance_route_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setenv("KIS_ACCOUNT_READ_ENABLED", "false")
    response = client.get("/api/v1/accounts/real/KR/balance")
    assert response.status_code == 503
    assert response.json()["detail"] == "KIS account reads are disabled"


def test_account_balance_route_requires_both_global_and_environment_switches(monkeypatch) -> None:
    monkeypatch.setenv("KIS_ACCOUNT_READ_ENABLED", "true")
    monkeypatch.setenv("KIS_REAL_ACCOUNT_READ_ENABLED", "false")
    response = client.get("/api/v1/accounts/real/KR/balance")
    assert response.status_code == 503
    assert response.json()["detail"] == "KIS account reads are disabled"


def test_account_balance_route_requires_global_switch_even_when_environment_is_enabled(monkeypatch) -> None:
    monkeypatch.setenv("KIS_ACCOUNT_READ_ENABLED", "false")
    monkeypatch.setenv("KIS_PAPER_ACCOUNT_READ_ENABLED", "true")
    response = client.get("/api/v1/accounts/paper/KR/balance")
    assert response.status_code == 503
    assert response.json()["detail"] == "KIS account reads are disabled"


def test_account_balance_route_rejects_incomplete_credentials_before_client_creation(monkeypatch) -> None:
    monkeypatch.setenv("KIS_ACCOUNT_READ_ENABLED", "true")
    monkeypatch.setenv("KIS_PAPER_ACCOUNT_READ_ENABLED", "true")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "configured-key")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "configured-secret")
    monkeypatch.setenv("KIS_PAPER_ACCOUNT_NUMBER", "123")
    monkeypatch.setattr(main, "KisReadOnlyClient", lambda *_args: pytest.fail("invalid account must not create a client"))

    response = client.get("/api/v1/accounts/paper/KR/balance")

    assert response.status_code == 503
    assert response.json()["detail"] == "KIS account credentials are not configured"


def test_account_kis_clients_reuse_tokens_per_environment_and_close_on_shutdown(monkeypatch) -> None:
    created = []

    class FakeKisClient:
        def __init__(self, settings):
            self.settings = settings
            self.closed = False
            created.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(main, "_KIS_CLIENTS", {})
    monkeypatch.setattr(main, "KisReadOnlyClient", FakeKisClient)
    monkeypatch.setenv("KIS_ENVIRONMENT", "paper")
    monkeypatch.setenv("KIS_MARKET_DATA_ENABLED", "true")
    for environment in ("PAPER", "REAL"):
        monkeypatch.setenv(f"KIS_{environment}_APP_KEY", f"{environment.lower()}-test-key")
        monkeypatch.setenv(f"KIS_{environment}_APP_SECRET", f"{environment.lower()}-test-secret")
        monkeypatch.setenv(f"KIS_{environment}_ACCOUNT_NUMBER", "1234567801")

    paper_first = main.get_kis_account_client("paper")
    paper_second = main.get_kis_account_client("paper")
    paper_quotes = main.get_kis_client()
    real = main.get_kis_account_client("real")

    assert paper_first is paper_second
    assert paper_first is paper_quotes
    assert paper_first is not real
    assert len(created) == 2
    main.close_kis_clients()
    assert all(client.closed for client in created)


def test_market_kis_client_is_closed_and_evicted_on_shutdown(monkeypatch) -> None:
    created = []

    class FakeKisClient:
        def __init__(self, settings):
            self.settings = settings
            self.closed = False
            created.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(main, "_KIS_CLIENTS", {})
    monkeypatch.setattr(main, "KisReadOnlyClient", FakeKisClient)
    monkeypatch.setenv("KIS_ENVIRONMENT", "paper")
    monkeypatch.setenv("KIS_MARKET_DATA_ENABLED", "true")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "paper-test-key")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "paper-test-secret")

    assert main.get_kis_client() is created[0]
    main.close_kis_clients()

    assert created[0].closed is True
    assert main._KIS_CLIENTS == {}


def test_quant_backtest_endpoint_accepts_supplied_bars_and_never_creates_orders() -> None:
    session_open = datetime(2026, 11, 5, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    response = client.post("/api/v1/strategies/orb-vwap/backtest", json={
        "market": "KR", "symbol": "005930", "buy_commission_bps": "2", "sell_commission_bps": "3",
        "sell_tax_bps": "4", "slippage_bps": "5", "bars": [
            {"timestamp": (session_open + timedelta(minutes=i)).isoformat(), "open": "100", "high": "101",
             "low": "99", "close": "100", "volume": 1000, "spread_bps": "5"}
            for i in range(380)
        ],
    })
    assert response.status_code == 200
    assert response.json()["executable"] is False
    assert response.json()["data_points"] == 380
    assert response.json()["trades"] == []
    assert response.json()["cost_assumptions_bps"] == {
        "buy_commission_per_fill": "2", "sell_commission_per_fill": "3",
        "sell_tax_per_fill": "4", "slippage_per_fill": "5",
    }


def test_quant_backtest_endpoint_requires_explicit_cost_assumptions() -> None:
    response = client.post("/api/v1/strategies/orb-vwap/backtest", json={
        "market": "KR", "symbol": "005930", "bars": [
            {"timestamp": "2026-11-05T09:00:00+09:00", "open": "100", "high": "101",
             "low": "99", "close": "100", "volume": 1000}
        ],
    })
    assert response.status_code == 422
    assert {item["loc"][-1] for item in response.json()["detail"]} >= {
        "buy_commission_bps", "sell_commission_bps", "sell_tax_bps", "slippage_bps",
    }


def test_quant_backtest_endpoint_rejects_all_zero_cost_assumptions() -> None:
    response = client.post("/api/v1/strategies/orb-vwap/backtest", json={
        "market": "KR", "symbol": "005930", "buy_commission_bps": "0", "sell_commission_bps": "0",
        "sell_tax_bps": "0", "slippage_bps": "0", "bars": [
            {"timestamp": "2026-11-05T15:19:00+09:00", "open": "100", "high": "101",
             "low": "99", "close": "100", "volume": 1000}
        ],
    })
    assert response.status_code == 422
    assert response.json()["detail"] == "at least one explicit trading cost must be positive"


def test_quant_backtest_endpoint_rejects_naive_timestamps() -> None:
    response = client.post("/api/v1/strategies/orb-vwap/backtest", json={
        "market": "KR", "symbol": "005930", "buy_commission_bps": "5", "sell_commission_bps": "5",
        "sell_tax_bps": "0", "slippage_bps": "5", "bars": [
            {"timestamp": "2026-11-05T09:00:00", "open": "100", "high": "101", "low": "99", "close": "100", "volume": 1000}
        ],
    })
    assert response.status_code == 422


def test_quant_backtest_endpoint_rejects_us_premarket_bars() -> None:
    response = client.post("/api/v1/strategies/orb-vwap/backtest", json={
        "market": "US", "symbol": "AAPL", "buy_commission_bps": "5", "sell_commission_bps": "5",
        "sell_tax_bps": "0", "slippage_bps": "5", "bars": [
            {"timestamp": f"2026-11-05T23:{i:02d}:00+09:00", "open": "100", "high": "101",
             "low": "99", "close": "100", "volume": 1000, "spread_bps": "5"}
            for i in range(3)
        ],
    })
    assert response.status_code == 422
    assert "official regular-session open" in response.json()["detail"]


def test_ai_prediction_endpoint_requires_full_relative_volume_lookback() -> None:
    response = client.post("/api/v1/ai/models/lightgbm-" + "a" * 32 + "/predict", json={
        "market": "KR", "symbol": "005930", "bars": [
            {"timestamp": f"2026-11-05T09:{i:02d}:00+09:00", "open": "100", "high": "101",
             "low": "99", "close": "100", "volume": 1000, "spread_bps": "5"}
            for i in range(20)
        ],
    })
    assert response.status_code == 422
