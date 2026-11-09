"""Local CSV export helpers for persisted minute-bar snapshots."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from .market_calendar import exchange_continuous_session_window


def summarize_session_coverage(
    rows: Sequence[tuple], *, market: str, require_continuous_session: bool = True,
) -> list[dict[str, object]]:
    """Report complete-minute and spread coverage by exchange session without changing data."""
    normalized_market = market.strip().upper()
    if normalized_market not in {"KR", "US"}:
        raise ValueError("market must be KR or US")
    zone = ZoneInfo("Asia/Seoul" if normalized_market == "KR" else "America/New_York")
    sessions: dict[date, list[tuple]] = {}
    for row in rows:
        if len(row) < 8 or row[0].tzinfo is None or row[0].utcoffset() is None:
            raise ValueError("session coverage requires timezone-aware timestamps and source")
        session_date = row[0].astimezone(zone).date()
        sessions.setdefault(session_date, []).append(row)

    summaries: list[dict[str, object]] = []
    for session_date, session_rows in sorted(sessions.items()):
        window = exchange_continuous_session_window(
            normalized_market,
            datetime.combine(session_date, datetime.min.time(), tzinfo=zone) + timedelta(hours=12),
        )
        if window is None:
            raise ValueError("session coverage contains a non-session date")
        opened, closed = window
        if require_continuous_session and any(
                not opened <= row[0].astimezone(timezone.utc) < closed for row in session_rows):
            raise ValueError("session coverage rows must fall inside a verified continuous session")
        expected_count = int((closed - opened).total_seconds() // 60)
        expected_timestamps = [opened + timedelta(minutes=index) for index in range(expected_count)]
        actual_timestamps = sorted(row[0].astimezone(timezone.utc) for row in session_rows)
        spread_count = sum(row[6] is not None for row in session_rows)
        source_counts: dict[str, int] = {}
        for row in session_rows:
            source = str(row[7]).strip() if row[7] is not None else ""
            if source:
                source_counts[source] = source_counts.get(source, 0) + 1
        summaries.append({
            "session_date": session_date.isoformat(),
            "bars": len(session_rows),
            "expected_bars": expected_count,
            "complete": actual_timestamps == expected_timestamps,
            "bars_with_spread": spread_count,
            "spread_complete": actual_timestamps == expected_timestamps and spread_count == expected_count,
            "sources": sorted({str(row[7]) for row in session_rows if row[7]}),
            "source_counts": dict(sorted(source_counts.items())),
        })
    return summaries


def validate_websocket_session_rows(rows: Sequence[tuple], *, market: str, expected_source: str) -> None:
    """Require exact, spread-complete regular-session bars from the expected KIS feed."""
    normalized_market = market.strip().upper()
    expected_sources = {"KR": "KIS_PAPER_WEBSOCKET", "US": "KIS_REAL_US_WEBSOCKET"}
    if normalized_market not in expected_sources or expected_source != expected_sources[normalized_market]:
        raise ValueError("market and approved KIS WebSocket source do not match")
    if not rows:
        raise ValueError("WebSocket session export contains no rows")
    zone = ZoneInfo("Asia/Seoul" if normalized_market == "KR" else "America/New_York")
    sessions: dict[date, list[tuple]] = {}
    for row in rows:
        if len(row) < 8 or row[0].tzinfo is None or row[0].utcoffset() is None:
            raise ValueError("WebSocket session rows require timezone-aware timestamps and source")
        if row[7] != expected_source:
            raise ValueError("session export contains an unexpected market-data source")
        if row[6] is None:
            raise ValueError("WebSocket session export requires spread data on every bar")
        sessions.setdefault(row[0].astimezone(zone).date(), []).append(row)

    for session_date, session_rows in sessions.items():
        window = exchange_continuous_session_window(
            normalized_market,
            datetime.combine(session_date, datetime.min.time(), tzinfo=zone) + timedelta(hours=12),
        )
        if window is None:
            raise ValueError("WebSocket export contains a non-session date")
        opened, closed = window
        expected_count = int((closed - opened).total_seconds() // 60)
        expected_timestamps = [opened + timedelta(minutes=index) for index in range(expected_count)]
        actual_timestamps = [row[0].astimezone(timezone.utc) for row in session_rows]
        if actual_timestamps != expected_timestamps:
            raise ValueError("WebSocket session must contain every expected minute exactly once")


def build_minute_bar_export(
    rows: Sequence[tuple],
    *,
    market: str,
    symbol: str,
    from_date: date,
    to_date: date,
    sources: Sequence[str],
    retrieved_at: datetime | None = None,
) -> tuple[bytes, dict[str, object]]:
    """Build a CSV and bind its source rights record to the exact exported bytes."""
    normalized_market = market.strip().upper()
    normalized_symbol = symbol.strip().upper()
    if normalized_market not in {"KR", "US"} or not normalized_symbol or from_date > to_date:
        raise ValueError("market, symbol, and an ordered date range are required")
    if not rows:
        raise ValueError("no stored minute bars matched the export filter")
    unique_sources = sorted({source.strip() for source in sources if source and source.strip()})
    row_sources = sorted({str(row[-1]).strip() for row in rows if len(row) >= 8 and row[-1]})
    if not unique_sources or not row_sources:
        raise ValueError("stored bar source information is required")
    if unique_sources != row_sources:
        raise ValueError("export source list must match the sources present in the exported rows")

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("symbol", "timestamp", "open", "high", "low", "close", "volume", "spread_bps"))
    for timestamp, open_price, high_price, low_price, close_price, volume, spread_bps, _source in rows:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("stored bar timestamps must include a timezone")
        writer.writerow((normalized_symbol, timestamp.isoformat(), str(open_price), str(high_price),
                         str(low_price), str(close_price), int(volume),
                         "" if spread_bps is None else str(spread_bps)))
    session_coverage = summarize_session_coverage(
        rows, market=normalized_market, require_continuous_session=False,
    )
    observed_sessions = [item["session_date"] for item in session_coverage]
    coverage = {
        "requested_from_date": from_date.isoformat(),
        "requested_to_date": to_date.isoformat(),
        "observed_session_count": len(session_coverage),
        "observed_first_session": observed_sessions[0] if observed_sessions else None,
        "observed_last_session": observed_sessions[-1] if observed_sessions else None,
        "observed_bar_count": len(rows),
        "expected_bar_count": sum(int(item["expected_bars"]) for item in session_coverage),
        "complete_session_count": sum(bool(item["complete"]) for item in session_coverage),
        "spread_complete_session_count": sum(bool(item["spread_complete"]) for item in session_coverage),
        "sessions": session_coverage,
    }
    csv_bytes = buffer.getvalue().encode("utf-8")
    retrieved = retrieved_at or datetime.now(timezone.utc).astimezone()
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        raise ValueError("export timestamp must include a timezone")
    digest = hashlib.sha256(csv_bytes).hexdigest()
    confirmed_kis_rights = all(source.upper().startswith("KIS_") for source in unique_sources)
    if confirmed_kis_rights:
        provider = "KIS Open API"
        license_or_terms = (
            "User-reported written KIS confirmation: personal local storage, backtesting, and model training; "
            "no public redistribution or third-party sharing"
        )
        terms_reference = "docs/kis-market-data-rights.md; confirmation scope recorded 2026-11-07"
        verified_by = "User-reported KIS written confirmation"
        verified_at = retrieved.isoformat()
        permissions = {"local_storage": True, "backtesting": True, "model_training": True}
    else:
        provider = "Local market snapshot database"
        license_or_terms = "REVIEW_REQUIRED"
        terms_reference = "REVIEW_REQUIRED"
        verified_by = None
        verified_at = None
        permissions = {"local_storage": False, "backtesting": False, "model_training": False}
    sidecar: dict[str, object] = {
        "schema_version": 1,
        "provider": provider,
        "dataset_name": f"{normalized_market} {normalized_symbol} minute bars {from_date.isoformat()} through {to_date.isoformat()}",
        "source_reference": "market_minute_bar sources: " + ", ".join(unique_sources),
        "license_or_terms": license_or_terms,
        "terms_reference": terms_reference,
        "retrieved_at": retrieved.isoformat(),
        "verified_by": verified_by,
        "verified_at": verified_at,
        "rights_reviewed": confirmed_kis_rights,
        "permissions": permissions,
        "coverage": coverage,
        "dataset_sha256": digest,
    }
    return csv_bytes, sidecar


def write_minute_bar_export(
    csv_path: str | Path,
    rows: Sequence[tuple],
    *,
    market: str,
    symbol: str,
    from_date: date,
    to_date: date,
    sources: Sequence[str],
) -> dict[str, object]:
    """Write CSV and its source-scoped, hash-bound rights sidecar."""
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_bytes, sidecar = build_minute_bar_export(
        rows, market=market, symbol=symbol, from_date=from_date, to_date=to_date, sources=sources,
    )
    sidecar_path = path.with_name(path.name + ".provenance.json")
    payloads = ((path, csv_bytes),
                (sidecar_path, json.dumps(sidecar, ensure_ascii=False, indent=2).encode("utf-8")))
    temporary_paths: list[Path] = []
    try:
        for destination, content in payloads:
            descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}-", suffix=".tmp", dir=path.parent)
            temporary_path = Path(temporary)
            temporary_paths.append(temporary_path)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        for temporary_path, (destination, _content) in zip(temporary_paths, payloads):
            os.replace(temporary_path, destination)
        return sidecar
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)
