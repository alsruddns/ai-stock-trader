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
