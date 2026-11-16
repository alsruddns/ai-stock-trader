from __future__ import annotations

import importlib.util
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.data_export import write_minute_bar_export
from app.market_calendar import exchange_continuous_session_window

REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPOSITORY / "scripts" / "import-historical-kr-training-bars.py"
SPEC = importlib.util.spec_from_file_location("historical_kr_training_import", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
IMPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMPORTER)


def _write_dataset(path: Path, *, missing_last_bar: bool = False) -> None:
    session = date(2026, 11, 2)
    window = exchange_continuous_session_window(
        "KR", datetime.combine(session, time(hour=12), tzinfo=ZoneInfo("Asia/Seoul")),
    )
    assert window is not None
    opened, closed = window
    bars = []
    timestamp = opened
    while timestamp < closed:
        bars.append((timestamp, Decimal("100"), Decimal("101"), Decimal("99"),
                     Decimal("100"), 10, None, None, IMPORTER.SOURCE))
        timestamp += timedelta(minutes=1)
    if missing_last_bar:
        bars.pop()
    write_minute_bar_export(
        path, bars, market="KR", symbol="005930", from_date=session, to_date=session,
        sources=[IMPORTER.SOURCE],
    )


def test_load_dataset_accepts_hash_bound_complete_kis_rest_session(tmp_path: Path) -> None:
    path = tmp_path / "kis-rest-kr-005930-20261102-20261102.csv"
    _write_dataset(path)

    digest, sessions = IMPORTER.load_dataset(path, "005930")

    assert len(digest) == 64
    assert list(sessions) == [date(2026, 11, 2)]
    assert len(sessions[date(2026, 11, 2)]) == 380
    assert all(bar.spread_bps is None and bar.spread_observed_at is None
               for bar in sessions[date(2026, 11, 2)])


def test_load_dataset_rejects_incomplete_session(tmp_path: Path) -> None:
    path = tmp_path / "kis-rest-kr-005930-20261102-20261102.csv"
    _write_dataset(path, missing_last_bar=True)

    with pytest.raises(ValueError, match="complete exchange OHLCV session"):
        IMPORTER.load_dataset(path, "005930")
