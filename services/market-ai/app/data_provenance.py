"""Operator-reviewed provenance for local market-data training and backtests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

_DATE_FIELDS = ("retrieved_at",)
_TEXT_FIELDS = ("provider", "dataset_name", "source_reference", "license_or_terms",
                "terms_reference")
_PERMISSIONS = ("local_storage", "backtesting", "model_training")
_PURPOSE_PERMISSION = {"backtesting": "backtesting", "model_training": "model_training"}
MIN_MODEL_TRAINING_SESSIONS = 13


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
) -> None:
    """Require exact, spread-complete sessions from the market's approved KIS WebSocket feed."""
    normalized_market = market.strip().upper()
    expected_source = {
        "KR": "KIS_PAPER_WEBSOCKET",
        "US": "KIS_REAL_US_WEBSOCKET",
    }.get(normalized_market)
    if expected_source is None:
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
    spread_complete_count = 0
    for session, bars in sessions.items():
        row = by_date[session.isoformat()]
        if (row.get("complete") is not True or row.get("spread_complete") is not True
                or not count_matches(row.get("bars"), len(bars))
                or not count_matches(row.get("expected_bars"), len(bars))
                or not count_matches(row.get("bars_with_spread"), len(bars))
                or row.get("sources") != [expected_source]
                or any(getattr(bar, "spread_bps", None) is None for bar in bars)):
            raise ValueError("model training requires complete, spread-covered sessions from the approved KIS WebSocket feed")
        complete_count += 1
        spread_complete_count += 1
    if (not count_matches(coverage.get("complete_session_count"), complete_count)
            or not count_matches(coverage.get("spread_complete_session_count"), spread_complete_count)):
        raise ValueError("model training session quality totals are inconsistent")
    if len(expected_dates) < MIN_MODEL_TRAINING_SESSIONS:
        raise ValueError(
            f"model training requires at least {MIN_MODEL_TRAINING_SESSIONS} eligible exchange sessions"
        )


def load_data_provenance(csv_path: str | Path, *, purpose: str) -> dict[str, Any]:
    """Read `<csv-name>.provenance.json` and bind it to the CSV's current bytes."""
    path = Path(csv_path)
    sidecar = path.with_name(path.name + ".provenance.json")
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"required data provenance file is missing: {sidecar.name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("data provenance file could not be read") from exc
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError("CSV file could not be read") from exc
    return validate_data_provenance(payload, expected_sha256=digest, purpose=purpose)
