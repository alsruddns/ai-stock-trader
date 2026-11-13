from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.paper_risk import baseline_capture_open, daily_loss_percent, paper_equity_snapshot


NOW = datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc)


def test_equity_snapshot_values_cash_and_fresh_positions_without_fx() -> None:
    result = paper_equity_snapshot(
        "KRW", cash_balances=[{"currency": "KRW", "balance": "800000"}],
        positions=[{"currency": "KRW", "market": "KR", "symbol": "005930", "quantity": 2}],
        quotes={("KR", "005930"): (Decimal("70000"), NOW - timedelta(seconds=1))},
        now=NOW, max_quote_age_seconds=3,
    )
    assert result.equity == Decimal("940000")
    assert result.reason is None


def test_equity_snapshot_fails_closed_when_any_position_mark_is_missing_or_stale() -> None:
    position = {"currency": "USD", "market": "US", "symbol": "AAPL", "quantity": 1}
    missing = paper_equity_snapshot("USD", cash_balances=[{"currency": "USD", "balance": "5000"}],
        positions=[position], quotes={}, now=NOW, max_quote_age_seconds=3)
    stale = paper_equity_snapshot("USD", cash_balances=[{"currency": "USD", "balance": "5000"}],
        positions=[position], quotes={("US", "AAPL"): (Decimal("200"), NOW - timedelta(seconds=4))},
        now=NOW, max_quote_age_seconds=3)
    assert missing.reason == "POSITION_QUOTE_MISSING"
    assert stale.reason == "POSITION_QUOTE_STALE"
    assert missing.equity is None and stale.equity is None


def test_daily_loss_and_capture_window_fail_closed() -> None:
    assert daily_loss_percent(Decimal("1000"), Decimal("990")) == Decimal("1.00")
    assert daily_loss_percent(Decimal("NaN"), Decimal("990")) is None
    assert daily_loss_percent(Decimal("1000"), Decimal("Infinity")) is None
    opened = NOW
    assert baseline_capture_open(market_open=opened, now=opened + timedelta(minutes=2))
    assert not baseline_capture_open(market_open=opened, now=opened + timedelta(minutes=4))
    assert daily_loss_percent(None, Decimal("990")) is None
