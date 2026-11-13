import csv
import hashlib
from datetime import date

import pytest

from app.lean_replay import map_utc_timestamp_to_replay_date, write_lean_replay_csv


def test_replay_date_mapping_preserves_utc_time_and_offset_conversion():
    assert map_utc_timestamp_to_replay_date(
        "2026-11-13T09:00:59+09:00", date(2026, 11, 12),
    ) == "2026-11-12T00:00:59+00:00"
    assert map_utc_timestamp_to_replay_date(
        "2026-11-13T10:00:59-05:00", date(2026, 11, 12),
    ) == "2026-11-12T15:00:59+00:00"


@pytest.mark.parametrize("value", ["2026-11-13T09:00:00", "not-a-timestamp"])
def test_replay_date_mapping_rejects_unzoned_or_invalid_values(value: str):
    with pytest.raises(ValueError):
        map_utc_timestamp_to_replay_date(value, date(2026, 11, 12))


def test_replay_csv_shifts_only_timestamp_columns_and_returns_staged_hash(tmp_path):
    source = tmp_path / "source.csv"
    destination = tmp_path / "lean.csv"
    source.write_text(
        "symbol,timestamp,close,spread_observed_at\n"
        "005930,2026-11-13T09:00:00+09:00,100,2026-11-13T09:00:59+09:00\n",
        encoding="utf-8",
    )

    staged_hash = write_lean_replay_csv(source, destination, date(2026, 11, 12))

    with destination.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows == [{
        "symbol": "005930",
        "timestamp": "2026-11-12T00:00:00+00:00",
        "close": "100",
        "spread_observed_at": "2026-11-12T00:00:59+00:00",
    }]
    assert staged_hash == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert source.read_text(encoding="utf-8").endswith("2026-11-13T09:00:59+09:00\n")
