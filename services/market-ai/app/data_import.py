"""Validated loader for user-supplied, permission-cleared minute-bar CSV data."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from pathlib import Path

from .quant import Market, MinuteBar, exchange_session_date

REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}


def load_minute_bar_sessions(
    csv_path: str | Path, *, market: Market, expected_symbol: str | None = None,
    source_bytes: bytes | None = None,
) -> dict[date, list[MinuteBar]]:
    """Load one instrument from a file or immutable byte snapshot and group its sessions."""
    sessions: dict[date, list[MinuteBar]] = defaultdict(list)
    if source_bytes is None:
        try:
            source = Path(csv_path).open("r", encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise ValueError("minute-bar CSV could not be opened") from exc
    else:
        if not isinstance(source_bytes, bytes):
            raise ValueError("minute-bar source snapshot must be bytes")
        try:
            source = StringIO(source_bytes.decode("utf-8-sig"), newline="")
        except UnicodeDecodeError as exc:
            raise ValueError("minute-bar CSV must be valid UTF-8") from exc

    with source:
        reader = csv.DictReader(source)
        normalized_fields = [str(column).strip().lower() for column in (reader.fieldnames or []) if column]
        if len(set(normalized_fields)) != len(normalized_fields):
            raise ValueError("minute-bar CSV contains duplicate column names")
        columns = set(normalized_fields)
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError(f"minute-bar CSV is missing required columns: {', '.join(missing)}")

        symbols: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            try:
                if None in row:
                    raise ValueError("row has more values than the header")
                normalized = {str(key).strip().lower(): value for key, value in row.items() if key}
                if any(normalized.get(column) is None or not normalized[column].strip()
                       for column in REQUIRED_COLUMNS):
                    raise ValueError("required value is empty")
                if "symbol" in columns and not normalized.get("symbol", "").strip():
                    raise ValueError("symbol is empty")
                if normalized.get("symbol", "").strip():
                    symbols.add(normalized["symbol"].strip().upper())
                timestamp = datetime.fromisoformat(normalized["timestamp"].strip())
                bar = MinuteBar(
                    timestamp=timestamp,
                    open=Decimal(normalized["open"]),
                    high=Decimal(normalized["high"]),
                    low=Decimal(normalized["low"]),
                    close=Decimal(normalized["close"]),
                    volume=int(normalized["volume"]),
                    spread_bps=(Decimal(normalized["spread_bps"])
                                if normalized.get("spread_bps", "").strip() else None),
                    spread_observed_at=(datetime.fromisoformat(normalized["spread_observed_at"].strip())
                                        if normalized.get("spread_observed_at", "").strip() else None),
                )
            except (ArithmeticError, InvalidOperation, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid minute-bar CSV row {line_number}: {exc}") from exc
            sessions[exchange_session_date(bar.timestamp, market)].append(bar)

    if not sessions:
        raise ValueError("minute-bar CSV contains no data rows")
    if len(symbols) > 1:
        raise ValueError("CSV must contain one instrument only; filter symbols before importing")
    if expected_symbol and symbols and symbols != {expected_symbol.strip().upper()}:
        raise ValueError("CSV symbol does not match the requested instrument")
    for session, bars in sessions.items():
        if any(exchange_session_date(bar.timestamp, market) != session for bar in bars):
            raise ValueError(f"timestamps must belong to exchange-local session {session}")
        if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:])):
            raise ValueError(f"timestamps must be strictly increasing within session {session}")
    return dict(sessions)
