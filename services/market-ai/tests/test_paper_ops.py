from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.paper_ops import (PaperOpsLimits, evaluate_paper_exit, evaluate_paper_gate,
                           exchange_session_window, size_paper_order, size_paper_order_diagnostic)
from app.quant import MinuteBar
from app.market_calendar import (exchange_continuous_session_window, exchange_session_dates,
                                 exchange_session_schedule, latest_closed_session_date)


def _good_gate(now: datetime, quote_at: datetime | None = None, **overrides):
    args = {
        "market": "KR", "now": now, "latest_quote_at": quote_at or now,
        "data_feed_healthy": True, "model_available": True, "model_approved": True, "broker_healthy": True,
        "daily_loss_pct": Decimal("0"), "consecutive_losses": 0, "open_positions": 0,
    }
    args.update(overrides)
    return evaluate_paper_gate(**args)


def test_calendar_identifies_domestic_weekday_session_and_holiday() -> None:
    local = timezone(timedelta(hours=9))
    weekday = datetime(2026, 11, 5, 10, 0, tzinfo=local)
    holiday = datetime(2026, 1, 1, 10, 0, tzinfo=local)
    window = exchange_session_window("KR", weekday)
    assert window is not None
    assert window[0] < weekday.astimezone(timezone.utc) < window[1]
    assert exchange_session_window("KR", holiday) is None


def test_domestic_session_range_uses_exchange_days_and_skips_weekend() -> None:
    assert exchange_session_dates("KR", date(2026, 11, 5), date(2026, 11, 9)) == (
        date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9),
    )
    schedule = exchange_session_schedule("KR", date(2026, 11, 5), date(2026, 11, 9))
    assert [item[0] for item in schedule] == [date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9)]
    assert all(item[1].tzinfo is not None and item[2].tzinfo is not None for item in schedule)


def test_latest_closed_session_uses_each_market_local_calendar_date() -> None:
    saturday_morning_kst = datetime(2026, 11, 7, 6, 0, tzinfo=timezone(timedelta(hours=9)))

    assert latest_closed_session_date("KR", saturday_morning_kst).isoformat() == "2026-11-06"
    assert latest_closed_session_date("US", saturday_morning_kst).isoformat() == "2026-11-06"


def test_latest_closed_session_skips_current_session_until_close() -> None:
    us_friday_open_kst = datetime(2026, 11, 7, 0, 0, tzinfo=timezone(timedelta(hours=9)))

    assert latest_closed_session_date("US", us_friday_open_kst).isoformat() == "2026-11-05"


def test_continuous_kr_session_excludes_the_closing_auction() -> None:
    local = timezone(timedelta(hours=9))
    at = datetime(2026, 10, 30, 12, 0, tzinfo=local)
    window = exchange_continuous_session_window("KR", at)
    assert window is not None
    assert window[0].astimezone(local).isoformat() == "2026-10-30T09:00:00+09:00"
    assert window[1].astimezone(local).isoformat() == "2026-10-30T15:20:00+09:00"


def test_korean_calendar_fails_closed_with_pandas_31(monkeypatch) -> None:
    import pandas as pd

    monkeypatch.setattr(pd, "__version__", "3.1.1")
    now = datetime(2026, 11, 5, 10, 0, tzinfo=timezone(timedelta(hours=9)))
    with pytest.raises(RuntimeError, match="requires pandas<3.1"):
        exchange_session_window("KR", now)
    state = _good_gate(now)
    assert state.session_open is False
    assert state.new_entries_allowed is False
    assert state.exit_management_allowed is False
    assert "MARKET_CALENDAR_UNAVAILABLE" in state.reasons


def test_us_calendar_uses_new_york_date_when_host_clock_is_korea_time() -> None:
    # 04:30 KST on Nov 6 is 14:30 EST on the prior NY date (Nov 5).
    korea_time = datetime(2026, 11, 6, 4, 30, tzinfo=timezone(timedelta(hours=9)))
    window = exchange_session_window("US", korea_time)
    assert window is not None
    state = evaluate_paper_gate(
        "US", korea_time, korea_time, data_feed_healthy=True, model_available=True,
        model_approved=True, broker_healthy=True, daily_loss_pct=Decimal("0"),
        consecutive_losses=0, open_positions=0,
    )
    assert state.session_open is True
    assert state.new_entries_allowed is True


def test_order_sizing_respects_risk_position_cash_and_broker_notional_caps() -> None:
    sized = size_paper_order(
        price=Decimal("100"), atr=Decimal("2"), equity=Decimal("100000"),
        available_cash=Decimal("9000"), max_order_notional=Decimal("10000"),
    )
    assert sized is not None
    assert sized.quantity == 83  # 0.25% risk budget / (ATR * 1.5 stop distance)
    assert sized.notional == Decimal("8300")
    assert sized.notional <= Decimal("100000") * Decimal("0.10")
    assert sized.notional <= Decimal("9000")

    cash_limited = size_paper_order(
        price=Decimal("100"), atr=Decimal("0.01"), equity=Decimal("100000"),
        available_cash=Decimal("250"), max_order_notional=Decimal("10000"),
    )
    assert cash_limited is not None and cash_limited.quantity == 2

    broker_limited = size_paper_order(
        price=Decimal("100"), atr=Decimal("0.01"), equity=Decimal("100000"),
        available_cash=Decimal("9000"), max_order_notional=Decimal("150"),
    )
    assert broker_limited is not None and broker_limited.quantity == 1


def test_order_sizing_diagnostic_identifies_each_cap_below_one_share() -> None:
    result = size_paper_order_diagnostic(
        price=Decimal("1100000"), atr=Decimal("2"), equity=Decimal("10000000"),
        available_cash=Decimal("10000000"), max_order_notional=Decimal("1000000"),
    )

    assert result.sizing is None
    assert result.limiting_constraints == ("POSITION_ALLOCATION", "ORDER_NOTIONAL")
    assert result.rejection_reason == "POSITION_ALLOCATION_AND_ORDER_NOTIONAL_LIMITS_BELOW_ONE_SHARE"
    assert size_paper_order(
        price=Decimal("1100000"), atr=Decimal("2"), equity=Decimal("10000000"),
        available_cash=Decimal("10000000"), max_order_notional=Decimal("1000000"),
    ) is None

    invalid = size_paper_order_diagnostic(
        price=Decimal("NaN"), atr=Decimal("2"), equity=Decimal("10000000"),
        available_cash=Decimal("10000000"), max_order_notional=Decimal("1000000"),
    )
    assert invalid.rejection_reason == "INVALID_SIZING_INPUT"

    non_positive = size_paper_order_diagnostic(
        price=Decimal("1100000"), atr=Decimal("2"), equity=Decimal("0"),
        available_cash=Decimal("10000000"), max_order_notional=Decimal("1000000"),
    )
    assert non_positive.rejection_reason == "NON_POSITIVE_SIZING_INPUT"


def test_order_sizing_fails_closed_without_funds_or_positive_inputs() -> None:
    assert size_paper_order(price=Decimal("100"), atr=Decimal("1"), equity=Decimal("0"),
                            available_cash=Decimal("1000"), max_order_notional=Decimal("1000")) is None
    assert size_paper_order(price=Decimal("100"), atr=Decimal("1"), equity=Decimal("1000"),
                            available_cash=Decimal("0"), max_order_notional=Decimal("1000")) is None


def test_paper_risk_limits_load_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_MAX_QUOTE_AGE_SECONDS", "4")
    monkeypatch.setenv("PAPER_MAX_SIGNAL_BAR_AGE_SECONDS", "90")
    monkeypatch.setenv("PAPER_DAILY_LOSS_LIMIT_PCT", "0.75")
    monkeypatch.setenv("PAPER_MAX_POSITION_EQUITY_PCT", "8")
    monkeypatch.setenv("PAPER_TRADE_RISK_EQUITY_PCT", "0.20")
    monkeypatch.setenv("PAPER_ATR_STOP_MULTIPLE", "2")
    monkeypatch.setenv("PAPER_MAX_OPEN_POSITIONS", "2")
    monkeypatch.setenv("PAPER_MAX_CONSECUTIVE_LOSSES", "2")
    monkeypatch.setenv("PAPER_FLATTEN_BEFORE_CLOSE_MINUTES", "7")

    limits = PaperOpsLimits.from_environment()
    assert limits.max_quote_age_seconds == 4
    assert limits.max_signal_bar_age_seconds == 90
    assert limits.max_daily_loss_pct == Decimal("0.75")
    assert limits.max_position_equity_pct == Decimal("8")
    assert limits.trade_risk_equity_pct == Decimal("0.20")
    assert limits.atr_stop_multiple == Decimal("2")
    assert limits.max_open_positions == 2
    assert limits.max_consecutive_losses == 2
    assert limits.flatten_before_close_minutes == 7


def test_malformed_paper_risk_setting_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("PAPER_DAILY_LOSS_LIMIT_PCT", "not-a-number")
    with pytest.raises(ValueError, match="risk settings are invalid"):
        PaperOpsLimits.from_environment()


def test_non_positive_signal_bar_age_limit_fails_closed() -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        PaperOpsLimits(max_signal_bar_age_seconds=0)


@pytest.mark.parametrize("override", [
    {"max_daily_loss_pct": Decimal("1.01")},
    {"max_position_equity_pct": Decimal("10.01")},
    {"trade_risk_equity_pct": Decimal("0.26")},
    {"max_open_positions": 4},
    {"max_consecutive_losses": 4},
])
def test_paper_risk_limits_reject_overrides_above_approved_guardrails(override) -> None:
    with pytest.raises(ValueError, match="cannot exceed the approved initial trading limits"):
        PaperOpsLimits(**override)


def test_paper_exit_uses_stop_first_and_worse_open_for_gaps() -> None:
    at = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)
    both_touched = MinuteBar(at, Decimal("100"), Decimal("105"), Decimal("95"), Decimal("102"), 1000, Decimal("5"))
    stop = evaluate_paper_exit(both_touched, stop_price=Decimal("98"), target_price=Decimal("104"))
    assert stop is not None and stop.reason == "ATR_STOP" and stop.price == Decimal("98")

    gap_down = MinuteBar(at, Decimal("95"), Decimal("101"), Decimal("94"), Decimal("100"), 1000, Decimal("5"))
    gap_stop = evaluate_paper_exit(gap_down, stop_price=Decimal("98"), target_price=Decimal("104"))
    assert gap_stop is not None and gap_stop.price == Decimal("95")


def test_paper_exit_handles_target_gap_session_flatten_and_hold() -> None:
    at = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)
    gap_up = MinuteBar(at, Decimal("105"), Decimal("106"), Decimal("100"), Decimal("105"), 1000, Decimal("5"))
    target = evaluate_paper_exit(gap_up, stop_price=Decimal("98"), target_price=Decimal("104"))
    assert target is not None and target.reason == "TAKE_PROFIT_GAP" and target.price == Decimal("105")

    quiet = MinuteBar(at, Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), 1000, Decimal("5"))
    close = evaluate_paper_exit(quiet, stop_price=Decimal("98"), target_price=Decimal("104"), flatten_required=True)
    assert close is not None and close.reason == "SESSION_END" and close.price == Decimal("101")
    assert evaluate_paper_exit(quiet, stop_price=Decimal("98"), target_price=Decimal("104")) is None


def test_new_entries_allowed_only_when_every_dependency_and_risk_gate_passes() -> None:
    now = datetime(2026, 11, 5, 10, 0, tzinfo=timezone(timedelta(hours=9)))
    state = _good_gate(now)
    assert state.session_open is True
    assert state.new_entries_allowed is True
    assert state.exit_management_allowed is True

    stale = _good_gate(now, now - timedelta(seconds=4))
    assert stale.new_entries_allowed is False
    assert "QUOTE_STALE" in stale.reasons

    future = _good_gate(now, now + timedelta(seconds=2))
    assert future.new_entries_allowed is False
    assert "QUOTE_TIMESTAMP_IN_FUTURE" in future.reasons

    tolerated_clock_skew = _good_gate(now, now + timedelta(seconds=1))
    assert tolerated_clock_skew.new_entries_allowed is True

    losses = _good_gate(now, daily_loss_pct=Decimal("1.00"), consecutive_losses=3)
    assert losses.new_entries_allowed is False
    assert {"DAILY_LOSS_LIMIT", "CONSECUTIVE_LOSS_LIMIT"}.issubset(losses.reasons)

    unapproved = _good_gate(now, model_approved=False)
    assert unapproved.new_entries_allowed is False
    assert "MODEL_NOT_APPROVED" in unapproved.reasons


def test_market_close_requires_flatten_and_blocks_new_entries() -> None:
    local = timezone(timedelta(hours=9))
    now = datetime(2026, 11, 5, 15, 27, tzinfo=local)
    state = _good_gate(now, limits=PaperOpsLimits(flatten_before_close_minutes=5))
    assert state.flatten_required is True
    assert state.new_entries_allowed is False
    assert "SESSION_ENDING" in state.reasons


def test_naive_timestamps_fail_closed() -> None:
    with pytest.raises(ValueError, match="timezone"):
        exchange_session_window("US", datetime(2026, 11, 5, 10, 0))
