"""Local CSV export helpers for persisted minute-bar snapshots."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Sequence


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
