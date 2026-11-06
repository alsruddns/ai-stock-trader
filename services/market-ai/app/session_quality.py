"""Read-only completeness and spread-coverage audit for persisted market sessions."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .market_calendar import exchange_continuous_session_window
from .quant import Market, MinuteBar


def meets_minimum_spread_coverage(actual_percent: object, minimum_percent: float) -> bool:
    """Fail closed when spread coverage is absent, invalid, or below the requested threshold."""
    if not math.isfinite(minimum_percent) or not 0 <= minimum_percent <= 100:
        raise ValueError("minimum spread coverage must be between 0 and 100 percent")
    if isinstance(actual_percent, bool) or not isinstance(actual_percent, (int, float)):
        return False
    return math.isfinite(actual_percent) and minimum_percent <= actual_percent <= 100


def audit_minute_bar_session(
    market: Market,
    symbol: str,
    session_date: date,
    bars: list[MinuteBar],
    *,
    evaluated_at: datetime | None = None,
) -> dict[str, object]:
    """Measure expected closed one-minute bars without filling or changing stored data."""
    normalized_market = market.upper()
    normalized_symbol = symbol.strip().upper()
    if normalized_market not in {"KR", "US"} or not normalized_symbol:
        raise ValueError("market and symbol are required")

    zone = ZoneInfo("Asia/Seoul" if normalized_market == "KR" else "America/New_York")
    session_noon = datetime.combine(session_date, time(hour=12), tzinfo=zone)
    window = exchange_continuous_session_window(normalized_market, session_noon)
    if window is None:
        return {
            "market": normalized_market, "symbol": normalized_symbol,
            "session_date": session_date.isoformat(), "status": "NON_SESSION",
            "expected_bars": 0, "completed_minutes": 0, "observed_bars": 0,
            "missing_bars": 0, "off_grid_bars": 0, "duplicate_bars": 0,
            "spread_bars": 0, "spread_coverage_percent": None,
            "first_bar_at": None, "last_bar_at": None, "full_session_complete": False,
        }

    opened, closed = (point.astimezone(timezone.utc) for point in window)
    expected_bars = int((closed - opened).total_seconds() // 60)
    if expected_bars <= 0 or (closed - opened).total_seconds() % 60:
        raise ValueError("exchange calendar returned a non-minute session window")

    now = evaluated_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("evaluation time must include a timezone")
    now = now.astimezone(timezone.utc)
    completed_minutes = max(0, min(expected_bars, int((now - opened).total_seconds() // 60)))
    session_closed = now >= closed

    timestamps: list[datetime] = []
    spread_count = 0
    off_grid_count = 0
    counts: dict[datetime, int] = {}
    expected_end = opened + timedelta(minutes=expected_bars)
    for bar in bars:
        timestamp = bar.timestamp
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("stored bar timestamp must include a timezone")
        instant = timestamp.astimezone(timezone.utc)
        if opened <= instant < expected_end:
            timestamps.append(instant)
            if bar.spread_bps is not None:
                spread_count += 1
            offset_seconds = (instant - opened).total_seconds()
            if offset_seconds % 60 or instant.second or instant.microsecond:
                off_grid_count += 1
                continue
            aligned = opened + timedelta(minutes=int(offset_seconds // 60))
            counts[aligned] = counts.get(aligned, 0) + 1

    observed_completed = sum(
        count for timestamp, count in counts.items()
        if timestamp < opened + timedelta(minutes=completed_minutes)
    )
    missing_bars = max(0, completed_minutes - len([
        timestamp for timestamp in counts
        if timestamp < opened + timedelta(minutes=completed_minutes)
    ]))
    duplicate_bars = sum(max(0, count - 1) for count in counts.values())
    fully_observed = (
        session_closed and completed_minutes == expected_bars
        and missing_bars == 0 and duplicate_bars == 0 and off_grid_count == 0
    )
    if now < opened:
        status = "PRE_OPEN"
    elif not session_closed:
        status = "COLLECTING"
    else:
        status = "COMPLETE" if fully_observed else "INCOMPLETE"

    ordered_timestamps = sorted(timestamps)
    spread_percent = (round(100 * spread_count / len(timestamps), 2) if timestamps else None)
    return {
        "market": normalized_market,
        "symbol": normalized_symbol,
        "session_date": session_date.isoformat(),
        "status": status,
        "session_open_at": opened.isoformat(),
        "session_close_at": closed.isoformat(),
        "expected_bars": expected_bars,
        "completed_minutes": completed_minutes,
        "observed_bars": len(timestamps),
        "observed_completed_bars": observed_completed,
        "missing_bars": missing_bars,
        "off_grid_bars": off_grid_count,
        "duplicate_bars": duplicate_bars,
        "spread_bars": spread_count,
        "spread_coverage_percent": spread_percent,
        "first_bar_at": ordered_timestamps[0].isoformat() if ordered_timestamps else None,
        "last_bar_at": ordered_timestamps[-1].isoformat() if ordered_timestamps else None,
        "full_session_complete": fully_observed,
    }
