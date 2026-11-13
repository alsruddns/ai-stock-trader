"""Date-only timestamp mapping for completed-session LEAN replays."""

from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path


def map_utc_timestamp_to_replay_date(value: str, replay_date: date) -> str:
    """Preserve the UTC time-of-day while mapping a bar to LEAN's allowed date."""
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("LEAN replay timestamps must be ISO-8601 values") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("LEAN replay timestamps must include a timezone")
    return instant.astimezone(timezone.utc).replace(
        year=replay_date.year, month=replay_date.month, day=replay_date.day,
    ).isoformat()


def write_lean_replay_csv(source: Path, destination: Path, replay_date: date) -> str:
    """Write a local LEAN-only CSV with timestamps shifted, returning its SHA-256."""
    import hashlib

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    digest = hashlib.sha256()
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as source_stream:
            reader = csv.DictReader(source_stream)
            if not reader.fieldnames or "timestamp" not in reader.fieldnames:
                raise ValueError("LEAN replay CSV must include a timestamp column")
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError("LEAN replay CSV contains duplicate columns")
            with temporary.open("w", encoding="utf-8", newline="") as target_stream:
                writer = csv.DictWriter(target_stream, fieldnames=reader.fieldnames, lineterminator="\n")
                writer.writeheader()
                for row in reader:
                    if not row.get("timestamp"):
                        raise ValueError("LEAN replay CSV contains a row without a timestamp")
                    row["timestamp"] = map_utc_timestamp_to_replay_date(row["timestamp"], replay_date)
                    if row.get("spread_observed_at"):
                        row["spread_observed_at"] = map_utc_timestamp_to_replay_date(
                            row["spread_observed_at"], replay_date,
                        )
                    writer.writerow(row)
                target_stream.flush()
                import os

                os.fsync(target_stream.fileno())
        with temporary.open("rb") as staged_stream:
            for chunk in iter(lambda: staged_stream.read(1024 * 1024), b""):
                digest.update(chunk)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return digest.hexdigest()
