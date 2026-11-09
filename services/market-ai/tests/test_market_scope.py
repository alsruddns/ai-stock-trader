from fastapi.testclient import TestClient

from app.main import app
from app.market_scope import require_cli_operational_market


client = TestClient(app)


def test_domestic_offline_command_scope_is_allowed_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MARKET_OPERATIONAL_SCOPE", raising=False)

    require_cli_operational_market("KR", "domestic CLI validation")


def test_domestic_analysis_remains_available_in_default_scope(monkeypatch) -> None:
    monkeypatch.delenv("MARKET_OPERATIONAL_SCOPE", raising=False)

    response = client.post("/api/v1/analysis/momentum", json={
        "market": "KR",
        "symbol": "005930",
        "bars": [{"close": 100}, {"close": 101}],
    })

    assert response.status_code == 200
    assert response.json()["market"] == "KR"


def test_overseas_quote_and_training_validation_are_blocked_before_external_or_database_access(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_OPERATIONAL_SCOPE", "KR")

    quote = client.get("/api/v1/market/quotes/US/AAPL?exchange=NAS")
    readiness = client.get("/api/v1/market/training-readiness/US/AAPL")

    assert quote.status_code == 423
    assert readiness.status_code == 423
