from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from app.ai_pipeline import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    LocalModelRegistry,
    TrainingSample,
    _chronological_partitions,
    _quant_baseline_metrics,
    build_training_samples,
    latest_features,
    _session_progress,
)
from app.quant import MinuteBar


def _bar(timestamp: datetime, close: Decimal, volume: int = 1000) -> MinuteBar:
    return MinuteBar(timestamp, close, close + Decimal("0.2"), close - Decimal("0.2"), close, volume, Decimal("5"))


def _sample_sessions() -> dict[date, list[MinuteBar]]:
    result: dict[date, list[MinuteBar]] = {}
    zone = timezone(timedelta(hours=9))
    for day in range(8):
        session_date = date(2026, 10, 1) + timedelta(days=day)
        start = datetime.combine(session_date, time(9), zone)
        bars = []
        for index in range(80):
            wave = Decimal(str(round(__import__("math").sin(index * 0.55 + day * 0.2), 4)))
            close = Decimal("100") + Decimal(day) / Decimal(10) + Decimal(index) / Decimal(100) + wave
            bars.append(_bar(start + timedelta(minutes=index), close, 1000 + index % 8 * 200))
        result[session_date] = bars
    return result


def _provenance(dataset_sha256: str) -> dict:
    return {
        "schema_version": 1, "provider": "test provider", "dataset_name": "synthetic unit fixture",
        "source_reference": "unit-test fixture", "license_or_terms": "test-only data",
        "terms_reference": "unit-test fixture", "retrieved_at": "2026-10-01T09:00:00+09:00",
        "verified_by": "unit test", "verified_at": "2026-10-01T09:00:00+09:00",
        "rights_reviewed": True,
        "permissions": {"local_storage": True, "backtesting": True, "model_training": True},
        "dataset_sha256": dataset_sha256,
    }


def test_features_at_time_t_match_online_inference_features() -> None:
    bars = _sample_sessions()[date(2026, 10, 1)][:26]
    samples = build_training_samples({bars[0].timestamp.date(): bars}, market="KR", horizon_bars=5, round_trip_cost_bps=Decimal("0"))
    at_t = next(sample for sample in samples if sample.timestamp == bars[20].timestamp.isoformat())
    online = latest_features(bars[:21], market="KR")
    assert len(online) == len(FEATURE_NAMES)
    assert at_t.features == online
    assert at_t.features[-1] == pytest.approx(20 / 390)
    with pytest.raises(ValueError, match="at least 21 bars"):
        latest_features(bars[:20], market="KR")


def test_us_training_and_inference_accept_one_new_york_session_crossing_korea_midnight() -> None:
    start = datetime(2026, 11, 5, 23, 30, tzinfo=timezone(timedelta(hours=9)))
    bars = [_bar(start + timedelta(minutes=index), Decimal("100") + Decimal(index) / 100)
            for index in range(30)]
    sessions = {date(2026, 11, 5): bars}

    samples = build_training_samples(
        sessions, market="US", horizon_bars=5, round_trip_cost_bps=Decimal("0"),
    )
    features = latest_features(bars[:21], market="US")

    assert samples
    assert samples[0].session == date(2026, 11, 5)
    assert len(features) == len(FEATURE_NAMES)


def test_session_progress_uses_market_open_and_exchange_timezone() -> None:
    assert _session_progress(datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc), "KR") == Decimal(0)
    assert _session_progress(datetime(2026, 11, 5, 3, 15, tzinfo=timezone.utc), "KR") == Decimal("0.5")
    assert _session_progress(datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc), "US") == Decimal(0)
    assert _session_progress(datetime(2026, 11, 5, 17, 45, tzinfo=timezone.utc), "US") == Decimal("0.5")
    assert _session_progress(datetime(2026, 11, 5, 21, 30, tzinfo=timezone.utc), "US") == Decimal(1)
    with pytest.raises(ValueError, match="market must be KR or US"):
        _session_progress(datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc), "JP")


def test_chronological_split_keeps_sessions_disjoint_and_ordered() -> None:
    samples = build_training_samples(_sample_sessions(), market="KR", horizon_bars=5, round_trip_cost_bps=Decimal("0"))
    train, validation, test = _chronological_partitions(samples)
    train_dates = {item.session for item in train}
    validation_dates = {item.session for item in validation}
    test_dates = {item.session for item in test}
    assert train_dates.isdisjoint(validation_dates | test_dates)
    assert validation_dates.isdisjoint(test_dates)
    assert max(train_dates) < min(validation_dates)
    assert max(validation_dates) < min(test_dates)


def test_quant_baseline_reports_candidate_coverage_and_cost_adjusted_label_rate() -> None:
    samples = [
        TrainingSample(date(2026, 10, 1), "t1", (), 1, True),
        TrainingSample(date(2026, 10, 1), "t2", (), 0, False),
        TrainingSample(date(2026, 10, 1), "t3", (), 0, True),
    ]

    metrics = _quant_baseline_metrics(samples)

    assert metrics == {
        "candidate_count": 2.0,
        "candidate_coverage": pytest.approx(2 / 3),
        "candidate_positive_rate": 0.5,
    }


def test_model_training_compares_both_models_and_keeps_them_unapproved(tmp_path) -> None:
    samples = build_training_samples(_sample_sessions(), market="KR", horizon_bars=5, round_trip_cost_bps=Decimal("0"))
    results = LocalModelRegistry(tmp_path).train_compare(
        samples, market="KR", dataset_sha256="a" * 64, dataset_provenance=_provenance("a" * 64),
    )
    assert {result.model_type for result in results} == {"lightgbm", "xgboost"}
    for result in results:
        metadata = LocalModelRegistry(tmp_path).manifest(result.model_id)
        assert metadata["approved"] is False
        assert metadata["artifact_sha256"] == result.artifact_sha256
        assert metadata["dataset_sha256"] == "a" * 64
        assert metadata["data_provenance"]["rights_reviewed"] is True
        assert LocalModelRegistry(tmp_path).list_models()[0]["data_rights_verified"] is True
        assert metadata["quant_baseline_test_metrics"] == result.quant_baseline_test_metrics
        assert "candidate_positive_rate" in result.test_metrics
        assert "candidate_coverage" in result.quant_baseline_test_metrics
        assert result.train_sessions[-1] < result.validation_sessions[0]
        assert result.validation_sessions[-1] < result.test_sessions[0]
        prediction = LocalModelRegistry(tmp_path).predict_preview(result.model_id, _sample_sessions()[date(2026, 10, 8)][:21], market="KR")
        assert 0 <= prediction["probability_positive"] <= 1
        assert prediction["market"] == "KR"
        assert prediction["approved"] is False
        assert prediction["executable"] is False
        with pytest.raises(ValueError, match="market scope"):
            LocalModelRegistry(tmp_path).predict_preview(result.model_id, _sample_sessions()[date(2026, 10, 8)][:21], market="US")


def test_training_rejects_malformed_dataset_digest_before_training(tmp_path) -> None:
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        LocalModelRegistry(tmp_path).train_compare(
            [], market="KR", dataset_sha256="not-a-digest", dataset_provenance={},
        )


def test_model_registry_rejects_path_traversal(tmp_path) -> None:
    with pytest.raises(ValueError, match="identifier"):
        LocalModelRegistry(tmp_path).manifest("../../secret")


def test_active_model_selection_fails_closed_for_corrupt_selection_file(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    registry._active_path.write_text("[]", encoding="utf-8")
    assert registry.active_approved_model("KR") is None


def test_registry_instances_for_same_model_directory_share_governance_lock(tmp_path) -> None:
    first = LocalModelRegistry(tmp_path)
    second = LocalModelRegistry(tmp_path / ".")
    separate = LocalModelRegistry(tmp_path / "other")

    assert first._governance_lock is second._governance_lock
    assert first._governance_lock is not separate._governance_lock


def test_legacy_model_with_outdated_feature_schema_cannot_be_approved_or_selected(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "c" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata.pop("feature_version")
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    registry._active_path.write_text(json.dumps({"model_id": model_id}), encoding="utf-8")

    assert registry.active_approved_model("KR") is None
    listed = next(item for item in registry.list_models() if item["model_id"] == model_id)
    assert listed["feature_compatible"] is False
    with pytest.raises(ValueError, match="feature schema is outdated"):
        registry.approve(model_id, approved_by="operator", note="Reviewed for migration but requires retraining first.")


def _write_candidate(registry: LocalModelRegistry, model_id: str) -> None:
    dataset_sha256 = "e" * 64
    artifact_name = f"{model_id}.joblib"
    artifact = registry.root / artifact_name
    artifact.write_bytes(model_id.encode())
    metadata = {
        "model_id": model_id, "market": "KR", "model_type": model_id.split("-")[0], "artifact": artifact_name,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "approved": False,
        "feature_names": list(FEATURE_NAMES), "feature_version": FEATURE_VERSION, "threshold": 0.5,
        "validation_metrics": {"accuracy": 0.6}, "test_metrics": {"accuracy": 0.55},
        "test_sessions": ["2026-10-08"], "dataset_sha256": dataset_sha256,
        "data_provenance": _provenance(dataset_sha256),
    }
    (registry.root / f"{model_id}.json").write_text(json.dumps(metadata), encoding="utf-8")


def test_model_without_data_rights_provenance_cannot_be_approved_or_selected(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "f" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata.pop("data_provenance")
    metadata["approved"] = True
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    registry._active_path.write_text(json.dumps({"model_id": model_id}), encoding="utf-8")

    listed = next(item for item in registry.list_models() if item["model_id"] == model_id)
    assert listed["data_rights_verified"] is False
    assert listed["approved"] is False
    assert listed["active"] is False
    with pytest.raises(ValueError, match="data-rights provenance"):
        registry.approve(model_id, approved_by="operator", note="Data source and permissions have not been documented.")
    assert registry.active_approved_model("KR") is None


def test_model_governance_requires_review_and_records_rollback(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    first = "lightgbm-" + "a" * 32
    second = "xgboost-" + "b" * 32
    _write_candidate(registry, first)
    _write_candidate(registry, second)

    with pytest.raises(ValueError, match="approved"):
        registry.activate(first, changed_by="operator", note="This note is long enough for an audit record.")
    with pytest.raises(ValueError, match="20 characters"):
        registry.approve(first, approved_by="operator", note="reviewed")

    registry.approve(first, approved_by="operator", note="Held-out sessions reviewed; keep as advisory only.")
    registry.activate(first, changed_by="operator", note="Approved after held-out test review; advisory only.")
    registry.approve(second, approved_by="operator", note="Second candidate reviewed against held-out sessions.")
    registry.activate(second, changed_by="operator", note="Promote second candidate after comparative review.")
    rollback = registry.activate(first, changed_by="operator", note="Rollback after monitoring found degraded metrics.", action="rollback")

    models = {item["model_id"]: item for item in registry.list_models()}
    assert models[first]["active"] is True
    assert models[first]["approved"] is True
    assert models[first]["market"] == "KR"
    assert models[second]["active"] is False
    assert rollback["previous_model_id"] == second
    assert registry.governance_events()[-1]["action"] == "rollback"
    assert registry.active_approved_model("KR")["model_id"] == first
    assert registry.active_approved_model("US") is None


def test_model_governance_rolls_back_state_when_audit_write_fails(tmp_path, monkeypatch) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "c" * 32
    _write_candidate(registry, model_id)

    def fail_audit(_event):
        raise OSError("simulated audit storage failure")

    monkeypatch.setattr(registry, "_record_governance_event", fail_audit)
    with pytest.raises(OSError, match="audit storage failure"):
        registry.approve(model_id, approved_by="operator", note="Held-out test evidence reviewed for advisory use.")
    assert registry.manifest(model_id)["approved"] is False

    monkeypatch.setattr(registry, "_record_governance_event", lambda _event: None)
    registry.approve(model_id, approved_by="operator", note="Held-out test evidence reviewed for advisory use.")
    registry.activate(model_id, changed_by="operator", note="Approved candidate selected for advisory use only.")
    replacement_id = "xgboost-" + "d" * 32
    _write_candidate(registry, replacement_id)
    registry.approve(replacement_id, approved_by="operator", note="Held-out test evidence reviewed for advisory use.")

    monkeypatch.setattr(registry, "_record_governance_event", fail_audit)
    with pytest.raises(OSError, match="audit storage failure"):
        registry.activate(replacement_id, changed_by="operator", note="Candidate change requires a durable audit event.")
    assert registry.active_approved_model("KR")["model_id"] == model_id
