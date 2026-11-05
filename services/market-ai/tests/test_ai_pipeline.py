from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

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
    results = LocalModelRegistry(tmp_path).train_compare(samples)
    assert {result.model_type for result in results} == {"lightgbm", "xgboost"}
    for result in results:
        metadata = LocalModelRegistry(tmp_path).manifest(result.model_id)
        assert metadata["approved"] is False
        assert metadata["artifact_sha256"] == result.artifact_sha256
        assert result.train_sessions[-1] < result.validation_sessions[0]
        assert result.validation_sessions[-1] < result.test_sessions[0]
        prediction = LocalModelRegistry(tmp_path).predict_preview(result.model_id, _sample_sessions()[date(2026, 10, 8)][:20])
        assert 0 <= prediction["probability_positive"] <= 1
        assert prediction["approved"] is False
        assert prediction["executable"] is False


def test_model_registry_rejects_path_traversal(tmp_path) -> None:
    with pytest.raises(ValueError, match="identifier"):
        LocalModelRegistry(tmp_path).manifest("../../secret")
