import hashlib
import json
from pathlib import Path
import runpy
import sys

import pytest

from app.data_provenance import load_data_provenance, validate_data_provenance


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


@pytest.mark.parametrize(
    ("script_name", "arguments"),
    [
        ("train-ai-models.py", ["--market", "KR", "--symbol", "005930"]),
        ("backtest-quant.py", ["--market", "KR", "--symbol", "005930"]),
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
