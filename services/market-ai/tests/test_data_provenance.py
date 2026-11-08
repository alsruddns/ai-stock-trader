import hashlib
import json
from pathlib import Path
import runpy
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.data_provenance import (
    load_data_provenance,
    validate_data_provenance,
    validate_model_training_coverage,
)


def _payload(digest: str) -> dict:
    return {
        "schema_version": 1,
        "provider": "sample provider",
        "dataset_name": "sample minute bars",
        "source_reference": "local test fixture",
        "license_or_terms": "test-only terms",
        "terms_reference": "test fixture reference",
        "retrieved_at": "2026-11-01T10:00:00+09:00",
        "verified_by": "test operator",
        "verified_at": "2026-11-02T10:00:00+09:00",
        "rights_reviewed": True,
        "permissions": {"local_storage": True, "backtesting": True, "model_training": True},
        "dataset_sha256": digest,
    }


def test_sidecar_is_bound_to_csv_bytes_and_required_workflow(tmp_path) -> None:
    csv = tmp_path / "bars.csv"
    csv.write_bytes(b"timestamp,open,high,low,close,volume\n")
    digest = hashlib.sha256(csv.read_bytes()).hexdigest()
    sidecar = csv.with_name(csv.name + ".provenance.json")
    sidecar.write_text(json.dumps(_payload(digest)), encoding="utf-8")

    result = load_data_provenance(csv, purpose="model_training")

    assert result["dataset_sha256"] == digest
    assert result["permissions"]["model_training"] is True


def test_missing_sidecar_fails_closed(tmp_path) -> None:
    csv = tmp_path / "bars.csv"
    csv.write_text("content", encoding="utf-8")
    with pytest.raises(ValueError, match="provenance file is missing"):
        load_data_provenance(csv, purpose="backtesting")


def test_changed_csv_digest_rejects_old_rights_record(tmp_path) -> None:
    csv = tmp_path / "bars.csv"
    csv.write_bytes(b"first content")
    digest = hashlib.sha256(csv.read_bytes()).hexdigest()
    (tmp_path / "bars.csv.provenance.json").write_text(json.dumps(_payload(digest)), encoding="utf-8")
    csv.write_bytes(b"changed content")

    with pytest.raises(ValueError, match="does not match"):
        load_data_provenance(csv, purpose="backtesting")


def test_provenance_can_be_bound_to_the_exact_bytes_used_for_parsing(tmp_path) -> None:
    csv = tmp_path / "bars.csv"
    csv.write_bytes(b"timestamp,open,high,low,close,volume\noriginal\n")
    source_snapshot = csv.read_bytes()
    digest = hashlib.sha256(source_snapshot).hexdigest()
    (tmp_path / "bars.csv.provenance.json").write_text(json.dumps(_payload(digest)), encoding="utf-8")
    csv.write_bytes(b"timestamp,open,high,low,close,volume\nchanged\n")

    result = load_data_provenance(csv, purpose="model_training", source_bytes=source_snapshot)

    assert result["dataset_sha256"] == digest


def test_rights_review_and_workflow_permission_are_mandatory() -> None:
    digest = "a" * 64
    payload = _payload(digest)
    payload["rights_reviewed"] = False
    with pytest.raises(ValueError, match="explicitly reviewed"):
        validate_data_provenance(payload, expected_sha256=digest, purpose="model_training")

    payload = _payload(digest)
    payload["permissions"]["model_training"] = False
    with pytest.raises(ValueError, match="permission is required"):
        validate_data_provenance(payload, expected_sha256=digest, purpose="model_training")


def test_timezone_is_required_for_retrieval_and_rights_review() -> None:
    digest = "b" * 64
    payload = _payload(digest)
    payload["verified_at"] = "2026-11-02T10:00:00"
    with pytest.raises(ValueError, match="include a timezone"):
        validate_data_provenance(payload, expected_sha256=digest, purpose="backtesting")


def test_model_training_requires_complete_spread_covered_approved_source_sessions() -> None:
    first_session = date(2026, 11, 2)
    session_dates = [first_session + timedelta(days=index) for index in range(13)]
    sessions = {session_date: [SimpleNamespace(spread_bps=5) for _ in range(390)]
                for session_date in session_dates}
    payload = _payload("c" * 64)
    payload["coverage"] = {
        "observed_session_count": 13,
        "observed_bar_count": 13 * 390,
        "complete_session_count": 13,
        "spread_complete_session_count": 13,
        "sessions": [
            {
                "session_date": session_date.isoformat(), "bars": 390, "expected_bars": 390,
                "complete": True, "bars_with_spread": 390, "spread_complete": True,
                "sources": ["KIS_REAL_US_WEBSOCKET"],
            }
            for session_date in session_dates
        ],
    }

    validate_model_training_coverage(payload, market="US", sessions=sessions)

    payload["coverage"]["sessions"][0]["sources"] = ["KIS_REST_MINUTE_SESSION"]
    with pytest.raises(ValueError, match="approved KIS WebSocket feed"):
        validate_model_training_coverage(payload, market="US", sessions=sessions)

    short_sessions = dict(list(sessions.items())[:-1])
    short_rows = [dict(row) for row in payload["coverage"]["sessions"][:12]]
    short_rows[0]["sources"] = ["KIS_REAL_US_WEBSOCKET"]
    short_coverage = dict(payload["coverage"])
    short_coverage["sessions"] = short_rows
    short_coverage["observed_session_count"] = 12
    short_coverage["observed_bar_count"] = 12 * 390
    short_coverage["complete_session_count"] = 12
    short_coverage["spread_complete_session_count"] = 12
    short_payload = {**payload, "coverage": short_coverage}
    with pytest.raises(ValueError, match="at least 13 eligible exchange sessions"):
        validate_model_training_coverage(short_payload, market="US", sessions=short_sessions)


def test_model_training_rejects_missing_or_partial_session_coverage() -> None:
    sessions = {date(2026, 11, 2): [SimpleNamespace(spread_bps=None)]}
    with pytest.raises(ValueError, match="requires exported session quality coverage"):
        validate_model_training_coverage(_payload("d" * 64), market="US", sessions=sessions)

    payload = _payload("d" * 64)
    payload["coverage"] = {
        "observed_session_count": 1, "observed_bar_count": 1,
        "complete_session_count": 1, "spread_complete_session_count": 1,
        "sessions": [{
            "session_date": "2026-11-02", "bars": 1, "expected_bars": 1,
            "complete": True, "bars_with_spread": 1, "spread_complete": True,
            "sources": ["KIS_REAL_US_WEBSOCKET"],
        }],
    }
    with pytest.raises(ValueError, match="complete, spread-covered sessions"):
        validate_model_training_coverage(payload, market="US", sessions=sessions)


@pytest.mark.parametrize(
    ("script_name", "arguments"),
    [
        ("train-ai-models.py", ["--market", "KR", "--symbol", "005930",
                                "--round-trip-cost-bps", "20", "--commission-bps", "5", "--slippage-bps", "5"]),
        ("backtest-quant.py", ["--market", "KR", "--symbol", "005930",
                               "--commission-bps", "5", "--slippage-bps", "5"]),
    ],
)
def test_data_clis_refuse_csv_without_rights_sidecar(tmp_path, monkeypatch, capsys, script_name, arguments) -> None:
    repository = Path(__file__).resolve().parents[3]
    csv = tmp_path / "bars.csv"
    csv.write_text("timestamp,open,high,low,close,volume\n", encoding="utf-8")
    script = repository / "scripts" / script_name
    monkeypatch.setattr(sys, "argv", [str(script), str(csv), *arguments])

    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(script), run_name="__main__")

    assert result.value.code == 2
    assert "required data provenance file is missing" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("script_name", "arguments", "missing_flag"),
    [
        ("train-ai-models.py", ["--market", "KR", "--symbol", "005930"], "--round-trip-cost-bps"),
        ("backtest-quant.py", ["--market", "KR", "--symbol", "005930"], "--commission-bps"),
    ],
)
def test_data_clis_require_explicit_cost_assumptions(
    tmp_path, monkeypatch, capsys, script_name, arguments, missing_flag
) -> None:
    repository = Path(__file__).resolve().parents[3]
    csv = tmp_path / "bars.csv"
    csv.write_text("timestamp,open,high,low,close,volume\n", encoding="utf-8")
    script = repository / "scripts" / script_name
    monkeypatch.setattr(sys, "argv", [str(script), str(csv), *arguments])

    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(script), run_name="__main__")

    assert result.value.code == 2
    assert f"the following arguments are required: {missing_flag}" in capsys.readouterr().err
