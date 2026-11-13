"""Exchange-session calendars shared by strategy and paper-operation gates."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

Market = Literal["KR", "US"]


def korean_calendar_compatibility() -> str:
    """Report whether installed ops dependencies are safe for XKRX sessions."""
    try:
        import exchange_calendars as calendars
        import pandas as pd
    except ImportError:
        return "DEPENDENCIES_MISSING"
    try:
        pandas_version = tuple(int(part) for part in pd.__version__.split(".")[:2])
        calendar_version = tuple(int(part) for part in calendars.__version__.split(".")[:3])
    except ValueError:
        return "VERSION_UNKNOWN"
    if pandas_version >= (3, 1):
        return "PANDAS_UNSUPPORTED"
    if calendar_version < (4, 13, 1):
        return "EXCHANGE_CALENDAR_UNSUPPORTED"
    return "COMPATIBLE"


def exchange_session_window(market: Market, now: datetime) -> tuple[datetime, datetime] | None:
    """Return the official exchange calendar open/close in UTC; unsupported dates fail closed."""
    if now.tzinfo is None:
        raise ValueError("current time must include a timezone")
    calendar_name = "XKRX" if market == "KR" else "XNYS" if market == "US" else None
    if calendar_name is None:
        raise ValueError("market must be KR or US")
    if market == "KR":
        compatibility = korean_calendar_compatibility()
        if compatibility == "PANDAS_UNSUPPORTED":
            raise RuntimeError("Korean market calendar requires pandas<3.1 until XKRX offset support is fixed")
        if compatibility != "COMPATIBLE":
            raise RuntimeError("Korean market calendar dependencies are unavailable or unsupported")
    exchange_zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    return _exchange_session_window_for_date(market, now.astimezone(exchange_zone).date())


@lru_cache(maxsize=128)
def exchange_session_dates(market: Market, start_date: date, end_date: date) -> tuple[date, ...]:
    """Return verified exchange dates for a bounded range using one calendar schedule."""
    if start_date > end_date:
        raise ValueError("session date range must be ordered")
    calendar_name = "XKRX" if market == "KR" else "XNYS" if market == "US" else None
    if calendar_name is None:
        raise ValueError("market must be KR or US")
    if market == "KR":
        compatibility = korean_calendar_compatibility()
        if compatibility == "PANDAS_UNSUPPORTED":
            raise RuntimeError("Korean market calendar requires pandas<3.1 until XKRX offset support is fixed")
        if compatibility != "COMPATIBLE":
            raise RuntimeError("Korean market calendar dependencies are unavailable or unsupported")
    try:
        import exchange_calendars as calendars
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Install the optional ops dependencies to use market sessions") from exc
    start_label = pd.Timestamp(start_date)
    end_label = pd.Timestamp(end_date)
    try:
        calendar = calendars.get_calendar(
            calendar_name, start=start_label - pd.Timedelta(days=7), end=end_label + pd.Timedelta(days=7),
        )
        return tuple(session.date() for session in calendar.sessions_in_range(start_label, end_label))
    except (KeyError, ValueError, IndexError) as exc:
        raise RuntimeError("market calendar could not verify this date range") from exc


def latest_closed_session_date(market: Market, now: datetime, lookback_days: int = 30) -> date:
    """Find the most recent exchange session whose close has passed, fail closed if unknown."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("current time must include a timezone")
    if not 1 <= lookback_days <= 366:
        raise ValueError("lookback_days must be between 1 and 366")
    exchange_zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    local_now = now.astimezone(exchange_zone)
    for days_ago in range(lookback_days):
        session_day = local_now.date() - timedelta(days=days_ago)
        local_noon = datetime.combine(session_day, datetime.min.time(), tzinfo=exchange_zone) + timedelta(hours=12)
        window = exchange_session_window(market, local_noon)
        if window is not None and window[1] <= now.astimezone(timezone.utc):
            return session_day
    raise RuntimeError("No closed exchange session found within the calendar lookback")


def exchange_continuous_session_window(market: Market, now: datetime) -> tuple[datetime, datetime] | None:
    """Return continuous trading bounds, excluding the KRX closing call auction."""
    window = exchange_session_window(market, now)
    if window is None:
        return None
    opened, closed = window
    if market == "KR":
        # KRX continuous matching ends 10 minutes before the official session close;
        # the remaining period is the closing single-price auction.
        closed -= timedelta(minutes=10)
    return opened, closed


@lru_cache(maxsize=512)
def _exchange_session_window_for_date(market: Market, session_date: date) -> tuple[datetime, datetime] | None:
    try:
        import exchange_calendars as calendars
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Install the optional ops dependencies to use market sessions") from exc
    calendar_name = "XKRX" if market == "KR" else "XNYS"
    label = pd.Timestamp(session_date)
    try:
        calendar = calendars.get_calendar(calendar_name, start=label - pd.Timedelta(days=10), end=label + pd.Timedelta(days=10))
        if not calendar.is_session(label):
            return None
        row = calendar.schedule.loc[label]
        opened = row["open"].to_pydatetime().astimezone(timezone.utc)
        closed = row["close"].to_pydatetime().astimezone(timezone.utc)
        return opened, closed
    except (KeyError, ValueError, IndexError) as exc:
        raise RuntimeError("market calendar could not verify this date") from exc
