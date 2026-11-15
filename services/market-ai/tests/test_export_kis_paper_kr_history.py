from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPOSITORY / "scripts" / "export-kis-paper-kr-history.py"
SPEC = importlib.util.spec_from_file_location("export_kis_paper_kr_history", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


def _session_bars(session_date: date, *, count: int = 380, spread_bps=None) -> list:
    from app.market_calendar import exchange_continuous_session_window

    opened, _closed = exchange_continuous_session_window(
        "KR", datetime.combine(session_date, datetime.min.time(), tzinfo=timezone.utc),
    )
    return [
        SimpleNamespace(timestamp=opened + timedelta(minutes=index), spread_bps=spread_bps)
        for index in range(count)
    ]


def test_complete_session_requires_every_ordered_minute_and_no_spread_claim() -> None:
    session_date = date(2026, 11, 5)

    assert EXPORTER._complete_session_bars(session_date, _session_bars(session_date)) is True
    assert EXPORTER._complete_session_bars(session_date, _session_bars(session_date, count=379)) is False
    assert EXPORTER._complete_session_bars(session_date, list(reversed(_session_bars(session_date)))) is False
    assert EXPORTER._complete_session_bars(session_date, _session_bars(session_date, spread_bps=1.2)) is False


def test_non_session_date_is_not_complete() -> None:
    assert EXPORTER._complete_session_bars(date(2026, 11, 7), []) is False
