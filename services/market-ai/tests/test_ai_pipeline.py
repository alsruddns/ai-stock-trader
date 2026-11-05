from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from app.ai_pipeline import (
    FEATURE_NAMES,
    LocalModelRegistry,
    _chronological_partitions,
    build_training_samples,
    latest_features,
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


def test_features_at_time_t_match_online_inference_features() -> None:
    bars = _sample_sessions()[date(2026, 10, 1)][:25]
    samples = build_training_samples({bars[0].timestamp.date(): bars}, horizon_bars=5, round_trip_cost_bps=Decimal("0"))
    at_t = next(sample for sample in samples if sample.timestamp == bars[19].timestamp.isoformat())
    online = latest_features(bars[:20])
    assert len(online) == len(FEATURE_NAMES)
    assert at_t.features == online


def test_chronological_split_keeps_sessions_disjoint_and_ordered() -> None:
    samples = build_training_samples(_sample_sessions(), horizon_bars=5, round_trip_cost_bps=Decimal("0"))
    train, validation, test = _chronological_partitions(samples)
    train_dates = {item.session for item in train}
    validation_dates = {item.session for item in validation}
    test_dates = {item.session for item in test}
    assert train_dates.isdisjoint(validation_dates | test_dates)
    assert validation_dates.isdisjoint(test_dates)
    assert max(train_dates) < min(validation_dates)
    assert max(validation_dates) < min(test_dates)


def test_model_training_compares_both_models_and_keeps_them_unapproved(tmp_path) -> None:
    samples = build_training_samples(_sample_sessions(), horizon_bars=5, round_trip_cost_bps=Decimal("0"))
    results = LocalModelRegistry(tmp_path).train_compare(samples, market="KR")
    assert {result.model_type for result in results} == {"lightgbm", "xgboost"}
    for result in results:
        metadata = LocalModelRegistry(tmp_path).manifest(result.model_id)
        assert metadata["approved"] is False
        assert metadata["artifact_sha256"] == result.artifact_sha256
        assert result.train_sessions[-1] < result.validation_sessions[0]
        assert result.validation_sessions[-1] < result.test_sessions[0]
        prediction = LocalModelRegistry(tmp_path).predict_preview(result.model_id, _sample_sessions()[date(2026, 10, 8)][:20], market="KR")
        assert 0 <= prediction["probability_positive"] <= 1
        assert prediction["market"] == "KR"
        assert prediction["approved"] is False
        assert prediction["executable"] is False
        with pytest.raises(ValueError, match="market scope"):
            LocalModelRegistry(tmp_path).predict_preview(result.model_id, _sample_sessions()[date(2026, 10, 8)][:20], market="US")


def test_model_registry_rejects_path_traversal(tmp_path) -> None:
    with pytest.raises(ValueError, match="identifier"):
        LocalModelRegistry(tmp_path).manifest("../../secret")


def test_active_model_selection_fails_closed_for_corrupt_selection_file(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    registry._active_path.write_text("[]", encoding="utf-8")
    assert registry.active_approved_model("KR") is None


def _write_candidate(registry: LocalModelRegistry, model_id: str) -> None:
    artifact_name = f"{model_id}.joblib"
    artifact = registry.root / artifact_name
    artifact.write_bytes(model_id.encode())
    metadata = {
        "model_id": model_id, "market": "KR", "model_type": model_id.split("-")[0], "artifact": artifact_name,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "approved": False,
        "feature_names": list(FEATURE_NAMES), "threshold": 0.5,
        "validation_metrics": {"accuracy": 0.6}, "test_metrics": {"accuracy": 0.55},
        "test_sessions": ["2026-10-08"],
    }
    (registry.root / f"{model_id}.json").write_text(json.dumps(metadata), encoding="utf-8")


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
