from fastapi.testclient import TestClient
from app.main import app, get_kis_client
from app.kis import Quote
from datetime import datetime, timezone
from decimal import Decimal

client = TestClient(app)


def test_health_discloses_read_only_mode() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["broker_connected"] is False
    assert response.json()["mode"] == "read-only-preview"
    assert response.json()["paper_automation_enabled"] is False
    assert response.json()["live_trading_enabled"] is False
    assert response.json()["paper_session_monitor"] == "DISABLED"


def test_paper_session_status_is_monitor_only() -> None:
    response = client.get("/api/v1/paper/session")
    assert response.status_code == 200
    assert response.json()["orders_enabled"] is False


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
    get_kis_client.cache_clear()
    response = client.get("/api/v1/market/quotes/KR/005930")
    assert response.status_code == 503
    assert response.json()["detail"] == "KIS market data is disabled"
    get_kis_client.cache_clear()


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


def test_quant_backtest_endpoint_accepts_supplied_bars_and_never_creates_orders() -> None:
    response = client.post("/api/v1/strategies/orb-vwap/backtest", json={
        "market": "KR", "symbol": "005930", "bars": [
            {"timestamp": f"2026-11-05T09:0{i}:00+09:00", "open": "100", "high": "101", "low": "99", "close": "100", "volume": 1000, "spread_bps": "5"}
            for i in range(3)
        ],
    })
    assert response.status_code == 200
    assert response.json()["executable"] is False
    assert response.json()["data_points"] == 3
    assert response.json()["trades"] == []


def test_quant_backtest_endpoint_rejects_naive_timestamps() -> None:
    response = client.post("/api/v1/strategies/orb-vwap/backtest", json={
        "market": "KR", "symbol": "005930", "bars": [
            {"timestamp": "2026-11-05T09:00:00", "open": "100", "high": "101", "low": "99", "close": "100", "volume": 1000}
        ],
    })
    assert response.status_code == 422


def test_ai_prediction_endpoint_requires_full_relative_volume_lookback() -> None:
    response = client.post("/api/v1/ai/models/lightgbm-" + "a" * 32 + "/predict", json={
        "market": "KR", "symbol": "005930", "bars": [
            {"timestamp": f"2026-11-05T09:{i:02d}:00+09:00", "open": "100", "high": "101",
             "low": "99", "close": "100", "volume": 1000, "spread_bps": "5"}
            for i in range(20)
        ],
    })
    assert response.status_code == 422
