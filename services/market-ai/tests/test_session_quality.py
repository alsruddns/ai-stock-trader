from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.market_calendar import exchange_continuous_session_window
from app.quant import MinuteBar
from app.session_quality import audit_minute_bar_session, meets_minimum_spread_coverage


def _bars(market: str, session_date: date, *, count: int, spreads: bool = True) -> list[MinuteBar]:
    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    opened, _closed = exchange_continuous_session_window(
        market, datetime.combine(session_date, datetime.min.time(), tzinfo=zone) + timedelta(hours=12),
    )
    return [MinuteBar(
        timestamp=opened + timedelta(minutes=index), open=Decimal("100"), high=Decimal("101"),
        low=Decimal("99"), close=Decimal("100"), volume=1000,
        spread_bps=Decimal("5") if spreads else None,
    ) for index in range(count)]


def test_complete_us_session_reports_full_coverage_and_spreads() -> None:
    session_date = date(2026, 11, 6)
    bars = _bars("US", session_date, count=390)
    report = audit_minute_bar_session(
        "US", "aapl", session_date, bars,
        evaluated_at=datetime(2026, 11, 7, 6, 1, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert report["status"] == "COMPLETE"
    assert report["expected_bars"] == 390
    assert report["observed_bars"] == 390
    assert report["missing_bars"] == 0
    assert report["spread_coverage_percent"] == 100
    assert report["full_session_complete"] is True


def test_closed_session_with_missing_opening_bar_is_not_complete() -> None:
    session_date = date(2026, 11, 6)
    bars = _bars("US", session_date, count=390)[1:]
    report = audit_minute_bar_session(
        "US", "AAPL", session_date, bars,
        evaluated_at=datetime(2026, 11, 7, 6, 1, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert report["status"] == "INCOMPLETE"
    assert report["missing_bars"] == 1
    assert report["missing_bar_timestamps"] == [
        datetime(2026, 11, 6, 14, 30, tzinfo=timezone.utc).isoformat(),
    ]
    assert report["first_bar_at"] == bars[0].timestamp.isoformat()
    assert report["full_session_complete"] is False


def test_quality_report_identifies_each_missing_bar_and_observed_bar_without_spread() -> None:
    session_date = date(2026, 11, 6)
    bars = _bars("US", session_date, count=390)
    bars = [bar for index, bar in enumerate(bars) if index != 1]
    bars[1] = MinuteBar(
        timestamp=bars[1].timestamp, open=bars[1].open, high=bars[1].high,
        low=bars[1].low, close=bars[1].close, volume=bars[1].volume, spread_bps=None,
    )
    report = audit_minute_bar_session(
        "US", "AAPL", session_date, bars,
        evaluated_at=datetime(2026, 11, 7, 6, 1, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert report["missing_bars"] == 1
    assert report["missing_bar_timestamps"] == [
        datetime(2026, 11, 6, 14, 31, tzinfo=timezone.utc).isoformat(),
    ]
    assert report["bars_without_spread_timestamps"] == [
        datetime(2026, 11, 6, 14, 32, tzinfo=timezone.utc).isoformat(),
    ]


def test_off_grid_bar_does_not_hide_missing_expected_minute() -> None:
    session_date = date(2026, 11, 6)
    bars = _bars("US", session_date, count=390)
    bars[0] = MinuteBar(
        timestamp=bars[0].timestamp + timedelta(seconds=20), open=bars[0].open,
        high=bars[0].high, low=bars[0].low, close=bars[0].close,
        volume=bars[0].volume, spread_bps=bars[0].spread_bps,
    )
    report = audit_minute_bar_session(
        "US", "AAPL", session_date, bars,
        evaluated_at=datetime(2026, 11, 7, 6, 1, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert report["status"] == "INCOMPLETE"
    assert report["off_grid_bars"] == 1
    assert report["missing_bars"] == 1
    assert report["full_session_complete"] is False


def test_duplicate_rows_do_not_inflate_spread_bar_count_or_coverage() -> None:
    session_date = date(2026, 11, 6)
    bars = _bars("US", session_date, count=390)
    bars.append(bars[0])

    report = audit_minute_bar_session(
        "US", "AAPL", session_date, bars,
        evaluated_at=datetime(2026, 11, 7, 6, 1, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert report["observed_bars"] == 391
    assert report["duplicate_bars"] == 1
    assert report["spread_bars"] == 390
    assert report["spread_coverage_percent"] == 100
    assert report["status"] == "INCOMPLETE"


def test_open_session_only_counts_elapsed_closed_minutes() -> None:
    session_date = date(2026, 11, 6)
    bars = _bars("US", session_date, count=4)
    bars[3] = MinuteBar(
        timestamp=bars[3].timestamp, open=bars[3].open, high=bars[3].high,
        low=bars[3].low, close=bars[3].close, volume=bars[3].volume, spread_bps=None,
    )
    report = audit_minute_bar_session(
        "US", "AAPL", session_date, bars,
        evaluated_at=datetime(2026, 11, 6, 9, 33, 30, tzinfo=ZoneInfo("America/New_York")),
    )

    assert report["status"] == "COLLECTING"
    assert report["completed_minutes"] == 3
    assert report["observed_bars"] == 4
    assert report["observed_completed_bars"] == 3
    assert report["spread_bars"] == 3
    assert report["spread_coverage_percent"] == 100
    assert report["full_session_complete"] is False


def test_kr_continuous_session_has_380_bars_and_does_not_count_closing_auction() -> None:
    session_date = date(2026, 11, 6)
    bars = _bars("KR", session_date, count=380, spreads=False)
    report = audit_minute_bar_session(
        "KR", "005930", session_date, bars,
        evaluated_at=datetime(2026, 11, 6, 16, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert report["expected_bars"] == 380
    assert report["observed_bars"] == 380
    assert report["spread_bars"] == 0
    assert report["spread_coverage_percent"] == 0
    assert report["full_session_complete"] is True
    assert report["bars_without_spread_timestamps"] == [
        bar.timestamp.isoformat() for bar in bars
    ]


def test_non_session_returns_non_session_without_fabricating_bars() -> None:
    report = audit_minute_bar_session(
        "US", "AAPL", date(2026, 11, 7), [],
        evaluated_at=datetime(2026, 11, 7, 12, tzinfo=timezone.utc),
    )

    assert report["status"] == "NON_SESSION"
    assert report["expected_bars"] == 0
    assert report["full_session_complete"] is False


def test_minimum_spread_coverage_gate_passes_at_threshold() -> None:
    assert meets_minimum_spread_coverage(95.0, 95.0) is True


def test_minimum_spread_coverage_gate_fails_closed_for_low_or_missing_coverage() -> None:
    assert meets_minimum_spread_coverage(94.99, 95.0) is False
    assert meets_minimum_spread_coverage(None, 0.0) is False


def test_minimum_spread_coverage_gate_rejects_invalid_threshold() -> None:
    try:
        meets_minimum_spread_coverage(100.0, 100.1)
    except ValueError as exc:
        assert "between 0 and 100" in str(exc)
    else:
        raise AssertionError("out-of-range threshold must be rejected")
