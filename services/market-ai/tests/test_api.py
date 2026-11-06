from fastapi.testclient import TestClient
from app import main
from app.main import app, get_kis_client
from app.kis import KisRateLimitError, KisSharedRateLimitUnavailable, Quote
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
    monkeypatch.setattr(main, "exchange_session_window", lambda _market, _now: (now - timedelta(minutes=30), now + timedelta(hours=1)))
    response = client.get("/api/v1/market/sessions/KR")
    assert response.status_code == 200
    body = response.json()
    assert body["market"] == "KR"
    assert body["is_session"] is True
    assert body["is_open"] is True
    assert body["open_at"] is not None
    assert body["close_at"] is not None
    assert body["evaluated_at"].endswith("+00:00")


def test_market_session_endpoint_reports_exchange_holiday(monkeypatch) -> None:
    monkeypatch.setattr(main, "exchange_session_window", lambda _market, _now: None)
    response = client.get("/api/v1/market/sessions/US")
    assert response.status_code == 200
    assert response.json()["is_session"] is False
    assert response.json()["is_open"] is False
    assert response.json()["open_at"] is None
    assert response.json()["close_at"] is None


def test_market_session_endpoint_fails_closed_when_calendar_unavailable(monkeypatch) -> None:
    def unavailable(_market, _now):
        raise RuntimeError("calendar unavailable")

    monkeypatch.setattr(main, "exchange_session_window", unavailable)
    response = client.get("/api/v1/market/sessions/KR")
    assert response.status_code == 503
    assert response.json()["detail"] == "Market calendar could not verify this session"


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
    assert response.json() == {"models": [], "governance_events": [], "executable": False}


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

    app.dependency_overrides[get_kis_client] = lambda: FakeKisClient()
    try:
        response = client.get("/api/v1/market/quotes/US/AAPL?exchange=NAS")
        assert response.status_code == 200
        assert response.json()["price"] == "231.59"
        assert response.json()["currency"] == "USD"
        assert response.json()["source"] == "KIS_REST_SNAPSHOT"
    finally:
        app.dependency_overrides.clear()


def test_kis_database_rate_limit_failure_returns_service_unavailable() -> None:
    class UnavailableKisClient:
        def get_quote(self, *_args, **_kwargs):
            raise KisSharedRateLimitUnavailable("shared KIS request pacing is unavailable")

    app.dependency_overrides[get_kis_client] = lambda: UnavailableKisClient()
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

    app.dependency_overrides[get_kis_client] = lambda: RateLimitedKisClient()
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
        def get_domestic_minute_session_bars(self, symbol, session_date, end_time):
            assert symbol == "005930"
            assert session_date == date(2026, 11, 5)
            assert end_time == "153000"
            return [MinuteBar(
                timestamp=datetime.fromisoformat("2026-11-05T09:00:00+09:00"), open=Decimal("100"),
                high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), volume=1000,
            )]

    app.dependency_overrides[get_kis_client] = lambda: FakeKisClient()
    try:
        response = client.get(
            "/api/v1/market/sessions/KR/005930/bars?session_date=2026-11-05",
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "KIS_REST_MINUTE_SESSION"
        assert body["spread_bps_available"] is False
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

    app.dependency_overrides[get_kis_client] = lambda: FakeKisClient()
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
        "market": "KR", "symbol": "005930", "bars": [
            {"timestamp": (session_open + timedelta(minutes=i)).isoformat(), "open": "100", "high": "101",
             "low": "99", "close": "100", "volume": 1000, "spread_bps": "5"}
            for i in range(380)
        ],
    })
    assert response.status_code == 200
    assert response.json()["executable"] is False
    assert response.json()["data_points"] == 380
    assert response.json()["trades"] == []


def test_quant_backtest_endpoint_rejects_naive_timestamps() -> None:
    response = client.post("/api/v1/strategies/orb-vwap/backtest", json={
        "market": "KR", "symbol": "005930", "bars": [
            {"timestamp": "2026-11-05T09:00:00", "open": "100", "high": "101", "low": "99", "close": "100", "volume": 1000}
        ],
    })
    assert response.status_code == 422


def test_quant_backtest_endpoint_rejects_us_premarket_bars() -> None:
    response = client.post("/api/v1/strategies/orb-vwap/backtest", json={
        "market": "US", "symbol": "AAPL", "bars": [
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
