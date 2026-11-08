from pathlib import Path

import pytest

from app.data_import import load_minute_bar_sessions


def _write_csv(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_minute_bar_loader_groups_valid_timezone_aware_sessions(tmp_path) -> None:
    path = _write_csv(tmp_path / "bars.csv", """timestamp,open,high,low,close,volume,spread_bps,symbol
2026-11-05T09:00:00+09:00,100,101,99,100,1000,5,005930
2026-11-05T09:01:00+09:00,100,102,99,101,1200,6,005930
2026-11-06T09:00:00+09:00,101,102,100,101,1100,5,005930
""")

    sessions = load_minute_bar_sessions(path, market="KR")

    assert list(map(str, sessions)) == ["2026-11-05", "2026-11-06"]
    assert len(sessions[next(iter(sessions))]) == 2
    assert sessions[next(iter(sessions))][0].spread_bps == 5


def test_minute_bar_loader_uses_the_supplied_immutable_source_snapshot(tmp_path) -> None:
    path = _write_csv(tmp_path / "snapshot.csv", """timestamp,open,high,low,close,volume,symbol
2026-11-05T09:00:00+09:00,100,101,99,100,1000,005930
""")
    snapshot = path.read_bytes()
    path.write_text("timestamp,open,high,low,close,volume,symbol\n"
                    "2026-11-05T09:00:00+09:00,200,201,199,200,1000,005930\n", encoding="utf-8")

    sessions = load_minute_bar_sessions(path, market="KR", expected_symbol="005930", source_bytes=snapshot)

    assert sessions[next(iter(sessions))][0].close == 100


@pytest.mark.parametrize("content, message", [
    ("timestamp,open,high,low,close,volume\n2026-11-05T09:00:00,100,101,99,100,1000\n", "timezone"),
    ("timestamp,open,high,low,close,volume\n2026-11-05T09:00:00+09:00,100,99,101,100,1000\n", "high/low"),
    ("timestamp,open,high,low,close,volume\n2026-11-05T09:00:00+09:00,100,101,99,100,1000\n2026-11-05T09:00:00+09:00,100,101,99,100,1000\n", "strictly increasing"),
    ("timestamp,open,high,low,close,volume,symbol\n2026-11-05T09:00:00+09:00,100,101,99,100,1000,\n", "symbol is empty"),
    ("timestamp,open,high,low,close,volume,volume\n2026-11-05T09:00:00+09:00,100,101,99,100,1000,1000\n", "duplicate column names"),
    ("timestamp,open,high,low,close,volume\n2026-11-05T09:00:00+09:00,100,101,99,100,1000\n2026-11-05T09:01:00+09:00,100,101,99,100,1000\n", "one instrument"),
])
def test_minute_bar_loader_rejects_invalid_rows_and_ambiguous_instruments(tmp_path, content, message) -> None:
    path = _write_csv(tmp_path / "invalid.csv", content)
    if "one instrument" in message:
        content = content.replace("volume\n", "volume,symbol\n").replace(",1000\n", ",1000,AAPL\n", 1)
        content = content.replace(",1000\n", ",1000,MSFT\n")
        path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_minute_bar_sessions(path, market="KR")


def test_minute_bar_loader_reports_missing_columns_and_empty_files(tmp_path) -> None:
    missing = _write_csv(tmp_path / "missing.csv", "timestamp,open\n")
    empty = _write_csv(tmp_path / "empty.csv", "timestamp,open,high,low,close,volume\n")

    with pytest.raises(ValueError, match="missing required columns"):
        load_minute_bar_sessions(missing, market="KR")
    with pytest.raises(ValueError, match="no data rows"):
        load_minute_bar_sessions(empty, market="KR")


def test_minute_bar_loader_checks_requested_instrument_when_csv_names_it(tmp_path) -> None:
    path = _write_csv(tmp_path / "symbol.csv", """timestamp,open,high,low,close,volume,symbol
2026-11-05T09:00:00+09:00,100,101,99,100,1000,AAPL
""")

    with pytest.raises(ValueError, match="does not match"):
        load_minute_bar_sessions(path, market="KR", expected_symbol="MSFT")


def test_us_bars_recorded_in_korea_time_remain_in_one_new_york_session(tmp_path) -> None:
    path = _write_csv(tmp_path / "us-bars.csv", """timestamp,open,high,low,close,volume,spread_bps,symbol
2026-11-05T23:30:00+09:00,100,101,99,100,1000,5,AAPL
2026-11-06T00:31:00+09:00,100,102,99,101,1200,6,AAPL
""")

    sessions = load_minute_bar_sessions(path, market="US", expected_symbol="AAPL")

    assert list(map(str, sessions)) == ["2026-11-05"]
    assert len(sessions[next(iter(sessions))]) == 2
