"""Exchange-session calendars shared by strategy and paper-operation gates."""

from __future__ import annotations

from datetime import date, datetime, timezone
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
