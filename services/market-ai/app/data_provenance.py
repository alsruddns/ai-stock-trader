"""Operator-reviewed provenance for local market-data training and backtests."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .market_calendar import exchange_session_schedule
from .session_quality import audit_minute_bar_session

_DATE_FIELDS = ("retrieved_at",)
_TEXT_FIELDS = ("provider", "dataset_name", "source_reference", "license_or_terms",
                "terms_reference")
_PERMISSIONS = ("local_storage", "backtesting", "model_training")
_PURPOSE_PERMISSION = {"backtesting": "backtesting", "model_training": "model_training"}
MIN_MODEL_TRAINING_SESSIONS = 13
APPROVED_MODEL_TRAINING_SOURCES = {
    "KR": frozenset({
        "KIS_PAPER_WEBSOCKET", "KIS_REST_MINUTE_SESSION", "KIS_PAPER_REST_MINUTE_CHART",
    }),
    "US": frozenset({"KIS_REAL_US_WEBSOCKET", "KIS_REST_MINUTE_SESSION"}),
}


def validate_data_provenance(
    payload: Any,
    *,
    expected_sha256: str,
    purpose: str,
) -> dict[str, Any]:
    """Validate a human-reviewed rights record bound to the exact CSV digest."""
    if purpose not in _PURPOSE_PERMISSION:
        raise ValueError("provenance purpose must be backtesting or model_training")
    if (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != 1):
        raise ValueError("data provenance must use schema_version 1")
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)):
        raise ValueError("expected dataset digest must be a lowercase SHA-256 value")
    if payload.get("dataset_sha256") != expected_sha256:
        raise ValueError("data provenance digest does not match the CSV file")

    normalized: dict[str, Any] = {"schema_version": 1, "dataset_sha256": expected_sha256}
    for field in _TEXT_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"data provenance field '{field}' is required")
        normalized[field] = value.strip()

    for field in _DATE_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"data provenance field '{field}' is required")
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"data provenance field '{field}' must be an ISO 8601 timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"data provenance field '{field}' must include a timezone")
        normalized[field] = parsed.isoformat()

    rights_reviewed = payload.get("rights_reviewed")
    if not isinstance(rights_reviewed, bool):
        raise ValueError("data provenance rights_reviewed must be true or false")
    if not rights_reviewed:
        raise ValueError("data rights must be explicitly reviewed before use")
    normalized["rights_reviewed"] = True

    reviewer = payload.get("verified_by")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("data provenance field 'verified_by' is required after rights review")
    normalized["verified_by"] = reviewer.strip()
    verified_at = payload.get("verified_at")
    if not isinstance(verified_at, str) or not verified_at.strip():
        raise ValueError("data provenance field 'verified_at' is required after rights review")
    try:
        verified_timestamp = datetime.fromisoformat(verified_at.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("data provenance field 'verified_at' must be an ISO 8601 timestamp") from exc
    if verified_timestamp.tzinfo is None or verified_timestamp.utcoffset() is None:
        raise ValueError("data provenance field 'verified_at' must include a timezone")
    normalized["verified_at"] = verified_timestamp.isoformat()

    permissions = payload.get("permissions")
    if not isinstance(permissions, dict):
        raise ValueError("data provenance permissions are required")
    normalized_permissions: dict[str, bool] = {}
    for permission in _PERMISSIONS:
        value = permissions.get(permission)
        if not isinstance(value, bool):
            raise ValueError(f"data provenance permission '{permission}' must be true or false")
        normalized_permissions[permission] = value
    if not normalized_permissions["local_storage"]:
        raise ValueError("local storage permission is required for this workflow")
    required_permission = _PURPOSE_PERMISSION[purpose]
    if not normalized_permissions[required_permission]:
        raise ValueError(f"'{required_permission}' permission is required for this workflow")
    normalized["permissions"] = normalized_permissions
    coverage = payload.get("coverage")
    if coverage is not None:
        if not isinstance(coverage, dict):
            raise ValueError("data provenance coverage must be an object")
        normalized["coverage"] = coverage
    return normalized


def validate_model_training_coverage(
    provenance: Mapping[str, Any], *, market: str, sessions: Mapping[Any, Sequence[Any]],
    minimum_sessions: int = MIN_MODEL_TRAINING_SESSIONS,
    require_spreads: bool = False,
) -> None:
    """Require complete sessions from approved KIS sources per market.

    Historical REST minute bars are suitable for OHLCV model training. They do
    not carry historical order-book spread, so spreads are audited separately
    as an execution/backtest input and are not fabricated for model features.
    """
    if type(minimum_sessions) is not int or minimum_sessions < 1:
        raise ValueError("minimum eligible session count must be a positive integer")
    normalized_market = market.strip().upper()
    approved_sources = APPROVED_MODEL_TRAINING_SOURCES.get(normalized_market)
    if approved_sources is None:
        raise ValueError("model training market must be KR or US")
    coverage = provenance.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("model training requires exported session quality coverage")
    quality_rows = coverage.get("sessions")
    if not isinstance(quality_rows, list):
        raise ValueError("model training session quality coverage is incomplete")
    def count_matches(value: Any, expected: int) -> bool:
        return type(value) is int and value == expected

    by_date: dict[str, dict[str, Any]] = {}
    for row in quality_rows:
        if not isinstance(row, dict) or not isinstance(row.get("session_date"), str):
            raise ValueError("model training session quality row is invalid")
        session_date = row["session_date"]
        if session_date in by_date:
            raise ValueError("model training session quality contains duplicate dates")
        by_date[session_date] = row

    expected_dates = {session.isoformat() for session in sessions}
    if (not expected_dates or set(by_date) != expected_dates
            or not count_matches(coverage.get("observed_session_count"), len(expected_dates))
            or not count_matches(coverage.get("observed_bar_count"), sum(len(bars) for bars in sessions.values()))):
        raise ValueError("model training session quality does not match the CSV sessions")
    complete_count = 0
    for session, bars in sessions.items():
        row = by_date[session.isoformat()]
        source_counts = row.get("source_counts")
        source_keys_valid = (
            isinstance(source_counts, dict)
            and all(isinstance(source, str) for source in source_counts)
        )
        sources = sorted(source_counts) if source_keys_valid else []
        source_counts_match = (
            bool(sources)
            and all(source in approved_sources for source in sources)
            and all(type(source_counts.get(source)) is int and source_counts[source] > 0 for source in sources)
            and sum(source_counts.values()) == len(bars)
        )
        if (row.get("complete") is not True
                or not count_matches(row.get("bars"), len(bars))
                or not count_matches(row.get("expected_bars"), len(bars))
                or row.get("sources") != sources
                or not source_counts_match
                or (require_spreads and (
                    row.get("spread_complete") is not True
                    or not count_matches(row.get("bars_with_spread"), len(bars))
                    or row.get("spread_observation_time_complete") is not True
                    or not count_matches(row.get("bars_with_spread_observation_time"), len(bars))
                    or any(getattr(bar, "spread_bps", None) is None
                           or getattr(bar, "spread_observed_at", None) is None for bar in bars)
                ))
                ):
            raise ValueError(
                "model data requires complete sessions from approved KIS sources and required spread coverage"
            )
        complete_count += 1
    if not count_matches(coverage.get("complete_session_count"), complete_count):
        raise ValueError("model training session quality totals are inconsistent")
    if len(expected_dates) < minimum_sessions:
        raise ValueError(
            f"model data requires at least {minimum_sessions} eligible exchange sessions"
        )


def validate_model_session_integrity(
    *, market: str, symbol: str, sessions: Mapping[Any, Sequence[Any]],
    require_spreads: bool = False,
) -> None:
    """Audit supplied bars against the exchange calendar, not only the exported manifest."""
    normalized_market = market.strip().upper()
    if normalized_market not in {"KR", "US"} or not symbol.strip():
        raise ValueError("model session market and symbol are required")
    if not isinstance(sessions, Mapping) or not sessions:
        raise ValueError("model sessions are required for completeness validation")

    evaluated_at = datetime.now(timezone.utc)
    session_dates = tuple(sessions)
    if any(type(session) is not date for session in session_dates):
        raise ValueError("model session keys must be exchange-local calendar dates")
    continuous_windows = {
        session_date: (
            opened,
            closed - timedelta(minutes=10) if normalized_market == "KR" else closed,
        )
        for session_date, opened, closed in exchange_session_schedule(
            normalized_market, min(session_dates), max(session_dates),
        )
    }
    for session, bars in sessions.items():
        if not isinstance(bars, Sequence) or isinstance(bars, (str, bytes)) or not bars:
            raise ValueError(f"model data requires a complete session with timestamped spreads: {session}")
        continuous_window = continuous_windows.get(session)
        if continuous_window is None:
            raise ValueError(f"model data contains a non-session exchange date: {session}")
        report = audit_minute_bar_session(
            normalized_market, symbol, session, list(bars), evaluated_at=evaluated_at,
            continuous_session_window=continuous_window,
        )
        expected = report.get("expected_bars")
        if (report.get("status") != "COMPLETE" or report.get("full_session_complete") is not True
                or report.get("observed_bars") != len(bars)
                or report.get("missing_bars") != 0 or report.get("off_grid_bars") != 0
                or report.get("duplicate_bars") != 0
                or (require_spreads and (
                    report.get("spread_bars") != expected
                    or report.get("spread_observation_time_bars") != expected
                ))):
            raise ValueError(
                f"model data requires a complete exchange OHLCV session with required spread coverage: {session}"
            )


def load_data_provenance(
    csv_path: str | Path, *, purpose: str, source_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Read `<csv-name>.provenance.json` and bind it to the parsed CSV byte snapshot."""
    path = Path(csv_path)
    sidecar = path.with_name(path.name + ".provenance.json")
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"required data provenance file is missing: {sidecar.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("data provenance file could not be read") from exc
    if source_bytes is not None and not isinstance(source_bytes, bytes):
        raise ValueError("CSV source snapshot must be bytes")
    if source_bytes is None:
        try:
            source_bytes = path.read_bytes()
        except OSError as exc:
            raise ValueError("CSV file could not be read") from exc
    digest = hashlib.sha256(source_bytes).hexdigest()
    return validate_data_provenance(payload, expected_sha256=digest, purpose=purpose)
