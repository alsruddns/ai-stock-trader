from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.paper_ops import PaperOpsLimits, evaluate_paper_gate, exchange_session_window


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


def test_new_entries_allowed_only_when_every_dependency_and_risk_gate_passes() -> None:
    now = datetime(2026, 11, 5, 10, 0, tzinfo=timezone(timedelta(hours=9)))
    state = _good_gate(now)
    assert state.session_open is True
    assert state.new_entries_allowed is True
    assert state.exit_management_allowed is True

    stale = _good_gate(now, now - timedelta(seconds=4))
    assert stale.new_entries_allowed is False
    assert "QUOTE_STALE" in stale.reasons

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
