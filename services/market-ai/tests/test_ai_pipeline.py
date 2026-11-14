from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace

import pytest

import app.ai_pipeline as ai_pipeline
from app.ai_pipeline import (
    BACKTEST_SPREAD_MODEL,
    BACKTEST_VERSION,
    FEATURE_NAMES,
    FEATURE_VERSION,
    LABEL_VERSION,
    LocalModelRegistry,
    TrainingSample,
    assess_training_samples,
    _chronological_partitions,
    _metrics,
    _quant_baseline_metrics,
    build_training_samples,
    latest_features,
    predict_probability_series,
    _session_progress,
)
from app.data_provenance import MIN_MODEL_TRAINING_SESSIONS
from app.quant import MinuteBar
from app.market_calendar import exchange_session_window


def _bar(timestamp: datetime, close: Decimal, volume: int = 1000) -> MinuteBar:
    return MinuteBar(timestamp, close, close + Decimal("0.2"), close - Decimal("0.2"), close, volume,
                     Decimal("5"), timestamp + timedelta(seconds=30))


def _sample_sessions() -> dict[date, list[MinuteBar]]:
    result: dict[date, list[MinuteBar]] = {}
    zone = timezone(timedelta(hours=9))
    calendar_day = date(2026, 10, 1)
    while len(result) < 8:
        session_date = calendar_day
        start = datetime.combine(session_date, time(9), zone)
        calendar_day += timedelta(days=1)
        if exchange_session_window("KR", start) is None:
            continue
        bars = []
        session_index = len(result)
        for index in range(380):
            wave = Decimal(str(round(__import__("math").sin(index * 0.55 + session_index * 0.2), 4)))
            close = Decimal("100") + Decimal(session_index) / Decimal(10) + Decimal(index) / Decimal(100) + wave
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
    all_bars = _sample_sessions()[date(2026, 10, 1)]
    bars = all_bars[:26]
    samples = build_training_samples({all_bars[0].timestamp.date(): all_bars}, market="KR", horizon_bars=5, round_trip_cost_bps=Decimal("20"))
    at_t = next(sample for sample in samples if sample.timestamp == bars[20].timestamp.isoformat())
    online = latest_features(bars[:21], market="KR")
    assert len(online) == len(FEATURE_NAMES)
    assert at_t.features == online
    assert at_t.features[-1] == pytest.approx(20 / 380)
    with pytest.raises(ValueError, match="at least 21 bars"):
        latest_features(bars[:20], market="KR")


def test_online_ema_features_use_full_regular_session_prefix(monkeypatch) -> None:
    bars = _sample_sessions()[date(2026, 10, 1)][:24]
    observed_lengths = []

    def record_ema(values, period):
        observed_lengths.append((len(values), period))
        return Decimal("100")

    monkeypatch.setattr(ai_pipeline, "_ema", record_ema)
    latest_features(bars, market="KR")

    assert observed_lengths == [(24, 9), (24, 20)]


def test_latest_features_reject_premarket_and_incomplete_minute_history() -> None:
    bars = _sample_sessions()[date(2026, 10, 1)][:21]
    premarket = datetime(2026, 10, 1, 8, 0, tzinfo=timezone(timedelta(hours=9)))
    shifted = [_bar(premarket + timedelta(minutes=index), bar.close, bar.volume) for index, bar in enumerate(bars)]
    with pytest.raises(ValueError, match="official regular-session open"):
        latest_features(shifted, market="KR")

    gap = [_bar(bar.timestamp + (timedelta(minutes=1) if index >= 10 else timedelta(0)),
                bar.close, bar.volume) for index, bar in enumerate(bars)]
    with pytest.raises(ValueError, match="without gaps"):
        latest_features(gap, market="KR")


def test_training_and_inference_require_timestamped_spread_observations() -> None:
    sessions = _sample_sessions()
    session = date(2026, 10, 1)
    bars = sessions[session]
    current = bars[20]
    bars[20] = MinuteBar(current.timestamp, current.open, current.high, current.low,
                         current.close, current.volume, current.spread_bps)

    with pytest.raises(ValueError, match="timestamped spread"):
        latest_features(bars[:21], market="KR")
    samples = build_training_samples(sessions, market="KR", horizon_bars=5,
                                     round_trip_cost_bps=Decimal("20"))
    assert not any(sample.session == session and sample.timestamp == current.timestamp.isoformat()
                   for sample in samples)

    bars[20] = _bar(current.timestamp, current.close, current.volume)
    future = bars[25]
    bars[25] = MinuteBar(future.timestamp, future.open, future.high, future.low,
                         future.close, future.volume, future.spread_bps)
    samples = build_training_samples(sessions, market="KR", horizon_bars=5,
                                     round_trip_cost_bps=Decimal("20"))
    assert not any(sample.session == session and sample.timestamp == current.timestamp.isoformat()
                   for sample in samples)


def test_probability_series_uses_only_current_and_prior_bars() -> None:
    bars = _sample_sessions()[date(2026, 10, 1)][:24]

    class RecordingModel:
        def __init__(self) -> None:
            self.features = []
            self.classes_ = [0, 1]

        def predict_proba(self, values):
            self.features.append(tuple(float(value) for value in values[0]))
            return [[0.25, 0.75]]

    model = RecordingModel()
    scores = predict_probability_series(model, bars, market="KR")

    assert list(scores) == [bar.timestamp.isoformat() for bar in bars[20:]]
    assert list(scores.values()) == [0.75] * 4
    assert len(model.features) == 4
    assert model.features[0] == pytest.approx(latest_features(bars[:21], market="KR"), abs=1e-7)
    assert model.features[-1] == pytest.approx(latest_features(bars[:24], market="KR"), abs=1e-7)


@pytest.mark.parametrize("probability", [float("nan"), -0.01, 1.01])
def test_probability_series_rejects_invalid_model_outputs(probability: float) -> None:
    bars = _sample_sessions()[date(2026, 10, 1)][:21]

    class InvalidModel:
        classes_ = [0, 1]

        def predict_proba(self, _values):
            return [[1 - probability, probability]]

    with pytest.raises(ValueError, match="invalid binary-class probabilities"):
        predict_probability_series(InvalidModel(), bars, market="KR")


def test_probability_series_rejects_non_normalized_model_outputs() -> None:
    bars = _sample_sessions()[date(2026, 10, 1)][:21]

    class InvalidModel:
        classes_ = [0, 1]

        def predict_proba(self, _values):
            return [[0.1, 0.5]]

    with pytest.raises(ValueError, match="invalid binary-class probabilities"):
        predict_probability_series(InvalidModel(), bars, market="KR")


def test_training_labels_deduct_observed_entry_and_exit_spread() -> None:
    sessions = _sample_sessions()
    session = date(2026, 10, 1)
    bars = sessions[session]
    timestamp = bars[20].timestamp.isoformat()
    with_spread = build_training_samples(sessions, market="KR", horizon_bars=5,
                                         round_trip_cost_bps=Decimal("20"))
    zero_spread_sessions = {
        day: [MinuteBar(bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume,
                        Decimal("0"), bar.timestamp + timedelta(seconds=30))
              for bar in rows]
        for day, rows in sessions.items()
    }
    without_spread = build_training_samples(zero_spread_sessions, market="KR", horizon_bars=5,
                                            round_trip_cost_bps=Decimal("20"))
    spread_sample = next(sample for sample in with_spread if sample.session == session and sample.timestamp == timestamp)
    zero_spread_sample = next(sample for sample in without_spread if sample.session == session and sample.timestamp == timestamp)

    assert spread_sample.forward_net_return_bps == pytest.approx(zero_spread_sample.forward_net_return_bps - 5)


def test_training_label_uses_next_bar_open_and_only_known_signal_and_exit_spreads() -> None:
    sessions = _sample_sessions()
    session = date(2026, 10, 1)
    bars = sessions[session]
    signal_index = 20
    entry_index = signal_index + 1
    exit_index = signal_index + 5
    entry = bars[entry_index]
    entry_open = bars[signal_index].close * Decimal("1.02")
    bars[entry_index] = MinuteBar(entry.timestamp, entry_open, max(entry.high, entry_open),
                                  entry.low, entry.close, entry.volume, Decimal("11"),
                                  entry.timestamp + timedelta(seconds=30))
    exit_bar = bars[exit_index]
    bars[exit_index] = MinuteBar(exit_bar.timestamp, exit_bar.open, exit_bar.high, exit_bar.low,
                                 exit_bar.close, exit_bar.volume, Decimal("13"),
                                 exit_bar.timestamp + timedelta(seconds=30))

    samples = build_training_samples(sessions, market="KR", horizon_bars=5,
                                     round_trip_cost_bps=Decimal("20"))
    sample = next(item for item in samples if item.session == session
                  and item.timestamp == bars[signal_index].timestamp.isoformat())
    expected = ((bars[exit_index].close / bars[entry_index].open - Decimal(1))
                * Decimal(10_000) - Decimal("20") - Decimal("9"))
    assert sample.forward_net_return_bps == pytest.approx(float(expected))
    assert sample.label == int(expected > 0)


def test_training_label_charges_signal_bar_spread_available_before_next_open() -> None:
    sessions = _sample_sessions()
    session = date(2026, 10, 1)
    bars = sessions[session]
    target = bars[20]
    bars[20] = MinuteBar(target.timestamp, target.open, target.high, target.low,
                         target.close, target.volume, Decimal("100"), target.timestamp + timedelta(seconds=30))

    samples = build_training_samples(sessions, market="KR", horizon_bars=5,
                                     round_trip_cost_bps=Decimal("20"))
    sample = next(item for item in samples if item.session == session
                  and item.timestamp == bars[20].timestamp.isoformat())
    baseline = _sample_sessions()[session]
    baseline_samples = build_training_samples({session: baseline}, market="KR", horizon_bars=5,
                                              round_trip_cost_bps=Decimal("20"))
    baseline_sample = next(item for item in baseline_samples if item.timestamp == baseline[20].timestamp.isoformat())
    assert sample.forward_net_return_bps == pytest.approx(baseline_sample.forward_net_return_bps - 47.5)
    assert sample.features[-2] == 100


def test_training_keeps_sample_when_next_bar_spread_is_missing() -> None:
    sessions = _sample_sessions()
    session = date(2026, 10, 1)
    entry = sessions[session][21]
    sessions[session][21] = MinuteBar(entry.timestamp, entry.open, entry.high, entry.low,
                                      entry.close, entry.volume, None)

    samples = build_training_samples(sessions, market="KR", horizon_bars=5,
                                     round_trip_cost_bps=Decimal("20"))
    assert any(sample.session == session
               and sample.timestamp == sessions[session][20].timestamp.isoformat()
               for sample in samples)


def test_training_skips_sample_when_signal_bar_spread_is_missing() -> None:
    sessions = _sample_sessions()
    session = date(2026, 10, 1)
    signal = sessions[session][20]
    sessions[session][20] = MinuteBar(signal.timestamp, signal.open, signal.high, signal.low,
                                      signal.close, signal.volume, None)

    samples = build_training_samples(sessions, market="KR", horizon_bars=5,
                                     round_trip_cost_bps=Decimal("20"))
    assert not any(sample.session == session
                   and sample.timestamp == sessions[session][20].timestamp.isoformat()
                   for sample in samples)


def test_training_skips_samples_with_missing_future_spread() -> None:
    sessions = _sample_sessions()
    session = date(2026, 10, 1)
    target = sessions[session][25]
    sessions[session][25] = MinuteBar(target.timestamp, target.open, target.high, target.low,
                                      target.close, target.volume, None)

    samples = build_training_samples(sessions, market="KR", horizon_bars=5,
                                     round_trip_cost_bps=Decimal("20"))

    assert not any(sample.session == session and sample.timestamp == sessions[session][20].timestamp.isoformat()
                   for sample in samples)


def test_training_sample_generation_rejects_cost_below_backtest_cost() -> None:
    with pytest.raises(ValueError, match="must cover modeled buy/sell fees, tax, and slippage"):
        build_training_samples(_sample_sessions(), market="KR", horizon_bars=5,
                               round_trip_cost_bps=Decimal("19.999"))


def test_us_training_and_inference_accept_one_new_york_session_crossing_korea_midnight() -> None:
    start = datetime(2026, 11, 5, 23, 30, tzinfo=timezone(timedelta(hours=9)))
    bars = [_bar(start + timedelta(minutes=index), Decimal("100") + Decimal(index) / 100)
            for index in range(390)]
    sessions = {date(2026, 11, 5): bars}

    samples = build_training_samples(
        sessions, market="US", horizon_bars=5, round_trip_cost_bps=Decimal("20"),
    )
    features = latest_features(bars[:21], market="US")

    assert samples
    assert samples[0].session == date(2026, 11, 5)
    assert len(features) == len(FEATURE_NAMES)


def test_kr_session_progress_uses_korean_market_open() -> None:
    assert _session_progress(datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc), "KR") == Decimal(0)
    assert _session_progress(datetime(2026, 11, 5, 3, 10, tzinfo=timezone.utc), "KR") == Decimal("0.5")
    assert _session_progress(datetime(2026, 11, 5, 6, 30, tzinfo=timezone.utc), "KR") == Decimal(1)
    with pytest.raises(ValueError, match="market must be KR"):
        _session_progress(datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc), "JP")


def test_us_session_progress_uses_new_york_exchange_session() -> None:
    assert _session_progress(datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc), "US") == Decimal(0)
    assert _session_progress(datetime(2026, 11, 5, 17, 45, tzinfo=timezone.utc), "US") == Decimal("0.5")
    assert _session_progress(datetime(2026, 11, 5, 21, 30, tzinfo=timezone.utc), "US") == Decimal(1)


def test_chronological_split_keeps_sessions_disjoint_and_ordered() -> None:
    samples = build_training_samples(_sample_sessions(), market="KR", horizon_bars=5, round_trip_cost_bps=Decimal("20"))
    train, validation, test = _chronological_partitions(samples)
    train_dates = {item.session for item in train}
    validation_dates = {item.session for item in validation}
    test_dates = {item.session for item in test}
    assert train_dates.isdisjoint(validation_dates | test_dates)
    assert validation_dates.isdisjoint(test_dates)
    assert max(train_dates) < min(validation_dates)
    assert max(validation_dates) < min(test_dates)


@pytest.mark.parametrize(("session_count", "expected_partition_sizes"), [
    (MIN_MODEL_TRAINING_SESSIONS, (5, 3, 5)),
    (14, (6, 3, 5)),
    (15, (7, 3, 5)),
    (25, (15, 5, 5)),
])
def test_approval_ready_partition_reserves_validation_and_five_test_sessions(
    session_count: int, expected_partition_sizes: tuple[int, int, int],
) -> None:
    samples = [
        TrainingSample(date(2026, 1, 1) + timedelta(days=index), str(index), (float(index),), index % 2)
        for index in range(session_count)
    ]

    partitions = _chronological_partitions(samples)

    assert tuple(len({sample.session for sample in partition}) for partition in partitions) == expected_partition_sizes
    dates = [{sample.session for sample in partition} for partition in partitions]
    assert dates[0].isdisjoint(dates[1] | dates[2])
    assert dates[1].isdisjoint(dates[2])
    assert max(dates[0]) < min(dates[1]) < min(dates[2])


def test_training_sample_readiness_matches_partition_and_class_gates() -> None:
    sessions = [date(2026, 1, 1) + timedelta(days=index) for index in range(MIN_MODEL_TRAINING_SESSIONS)]
    samples = [
        TrainingSample(session, f"{session}:{index}", (float(index),), index % 2)
        for session in sessions for index in range(40)
    ]

    ready = assess_training_samples(samples)
    assert ready["ready"] is True
    assert ready["sample_count"] == 520
    assert ready["partition_sample_counts"] == {"train": 200, "validation": 120, "test": 200}
    assert ready["partition_session_counts"] == {"train": 5, "validation": 3, "test": 5}
    assert ready["session_class_counts"] == [
        {
            "session_date": session.isoformat(),
            "sample_count": 40,
            "positive": 20,
            "negative": 20,
        }
        for session in sessions
    ]
    assert ready["partition_class_counts"] == {
        "train": {"positive": 100, "negative": 100},
        "validation": {"positive": 60, "negative": 60},
        "test": {"positive": 100, "negative": 100},
    }

    one_class_training = [
        TrainingSample(sample.session, sample.timestamp, sample.features,
                       0 if sample.session in set(sessions[:5]) else sample.label)
        for sample in samples
    ]
    unbalanced = assess_training_samples(one_class_training)
    assert unbalanced["ready"] is False
    assert unbalanced["reasons"] == ["training_partition_requires_both_outcome_classes"]
    assert unbalanced["partition_class_counts"]["train"] == {"positive": 0, "negative": 200}

    sparse_samples = [
        TrainingSample(session, f"{session}:{index}", (float(index),), index % 2)
        for session in sessions for index in range(4)
    ]
    sparse = assess_training_samples(sparse_samples)
    assert sparse["ready"] is False
    assert sparse["reasons"] == ["insufficient_samples_per_partition"]


def test_quant_baseline_reports_candidate_coverage_and_cost_adjusted_label_rate() -> None:
    samples = [
        TrainingSample(date(2026, 10, 1), "t1", (), 1, True, 20.0),
        TrainingSample(date(2026, 10, 1), "t2", (), 0, False, -5.0),
        TrainingSample(date(2026, 10, 1), "t3", (), 0, True, -10.0),
    ]

    metrics = _quant_baseline_metrics(samples)

    assert metrics == {
        "candidate_count": 2.0,
        "candidate_coverage": pytest.approx(2 / 3),
        "candidate_positive_rate": 0.5,
        "candidate_mean_forward_net_bps": 5.0,
    }


def test_ai_candidate_metrics_apply_the_same_quant_gate_as_paper_runtime() -> None:
    class FixedModel:
        classes_ = [0, 1]

        def predict_proba(self, _features):
            import numpy as np
            return np.asarray([[0.1, 0.9], [0.2, 0.8], [0.01, 0.99], [0.9, 0.1]])

    samples = [
        TrainingSample(date(2026, 10, 1), "t1", (0.0,), 1, True, 10.0),
        TrainingSample(date(2026, 10, 1), "t2", (0.0,), 0, True, -5.0),
        TrainingSample(date(2026, 10, 1), "t3", (0.0,), 1, False, 20.0),
        TrainingSample(date(2026, 10, 1), "t4", (0.0,), 1, True, 15.0),
    ]

    metrics = _metrics(FixedModel(), samples)

    assert metrics["candidate_count"] == 2.0
    assert metrics["candidate_coverage"] == 0.5
    assert metrics["candidate_positive_rate"] == 0.5
    assert metrics["candidate_mean_forward_net_bps"] == 2.5


def test_ai_metrics_leave_roc_auc_unavailable_for_single_class_evaluation() -> None:
    model = SimpleNamespace(classes_=[0, 1], predict_proba=lambda _features: [[0.2, 0.8]])
    samples = [TrainingSample(date(2026, 10, 1), "t1", (0.0,), 1, True, 10.0)]

    metrics = _metrics(model, samples)

    assert metrics["roc_auc"] is None
    assert metrics["positive_recall"] == 1.0
    assert metrics["specificity"] is None
    assert metrics["balanced_accuracy"] is None


def test_ai_metrics_expose_rare_positive_failures_hidden_by_accuracy() -> None:
    model = SimpleNamespace(
        classes_=[0, 1],
        predict_proba=lambda features: [[0.99, 0.01] for _ in features],
    )
    samples = [
        TrainingSample(date(2026, 10, 1), f"t{index}", (float(index),), int(index == 0))
        for index in range(100)
    ]

    metrics = _metrics(model, samples)

    assert metrics["accuracy"] == 0.99
    assert metrics["positive_label_rate"] == 0.01
    assert metrics["predicted_positive_rate"] == 0.0
    assert metrics["positive_precision"] is None
    assert metrics["positive_recall"] == 0.0
    assert metrics["specificity"] == 1.0
    assert metrics["balanced_accuracy"] == 0.5


def test_ai_candidate_metrics_reject_malformed_binary_probabilities() -> None:
    samples = [
        TrainingSample(date(2026, 10, 1), "t1", (0.0,), 1, True, 10.0),
        TrainingSample(date(2026, 10, 1), "t2", (0.0,), 0, True, -5.0),
    ]
    invalid_outputs = (
        [[0.2, 0.9], [0.3, 0.7]],
        [[0.5, 0.5]],
        [[0.5, 0.5], [float("nan"), float("nan")]],
    )
    for output in invalid_outputs:
        model = SimpleNamespace(classes_=[0, 1], predict_proba=lambda _features, result=output: result)
        with pytest.raises(ValueError, match="binary-class probability|invalid binary-class probabilities"):
            _metrics(model, samples)

    reversed_classes = SimpleNamespace(
        classes_=[1, 0], predict_proba=lambda _features: [[0.5, 0.5]],
    )
    with pytest.raises(ValueError, match="classes must be ordered"):
        ai_pipeline._positive_probability(reversed_classes, (0.0,))
    missing_classes = SimpleNamespace(predict_proba=lambda _features: [[0.5, 0.5]])
    with pytest.raises(ValueError, match="classes must be ordered"):
        ai_pipeline._positive_probability(missing_classes, (0.0,))


def test_model_training_compares_both_models_and_keeps_them_unapproved(tmp_path, monkeypatch) -> None:
    sessions = _sample_sessions()
    generated_from = []
    original_builder = ai_pipeline.build_training_samples

    def recording_builder(source_sessions, **kwargs):
        generated_from.append(source_sessions)
        return original_builder(source_sessions, **kwargs)

    monkeypatch.setattr(ai_pipeline, "build_training_samples", recording_builder)
    results = LocalModelRegistry(tmp_path).train_compare(
        market="KR", symbol="005930", sessions=sessions,
        label_horizon_bars=5, label_round_trip_cost_bps=Decimal("20"),
        dataset_sha256="a" * 64, dataset_provenance=_provenance("a" * 64),
    )
    assert generated_from == [sessions]
    assert {result.model_type for result in results} == {"lightgbm", "xgboost"}
    for result in results:
        metadata = LocalModelRegistry(tmp_path).manifest(result.model_id)
        assert metadata["approved"] is False
        assert "heldout_evaluation" not in metadata
        assert LocalModelRegistry(tmp_path)._has_valid_approval_evidence(metadata) is False
        assert metadata["artifact_sha256"] == result.artifact_sha256
        assert metadata["dataset_sha256"] == "a" * 64
        assert metadata["data_provenance"]["rights_reviewed"] is True
        assert LocalModelRegistry(tmp_path).list_models()[0]["data_rights_verified"] is True
        assert metadata["quant_baseline_test_metrics"] == result.quant_baseline_test_metrics
        assert metadata["quant_backtest_test_metrics"] == result.quant_backtest_test_metrics
        assert metadata["ai_quant_backtest_test_metrics"] == result.ai_quant_backtest_test_metrics
        assert metadata["quant_backtest_test_metrics"]["session_count"] == len(result.test_sessions)
        assert isinstance(metadata["ai_quant_backtest_test_metrics"]["net_pnl"], str)
        assert metadata["training_config"]["symbol"] == "005930"
        assert metadata["training_config"]["label_horizon_bars"] == 5
        assert metadata["training_config"]["backtest_buy_commission_bps"] == "5"
        assert metadata["training_config"]["backtest_sell_commission_bps"] == "5"
        assert metadata["training_config"]["backtest_sell_tax_bps"] == "0"
        assert metadata["training_config"]["backtest_spread_model"] == BACKTEST_SPREAD_MODEL
        assert "candidate_positive_rate" in result.test_metrics
        assert "candidate_mean_forward_net_bps" in result.test_metrics
        assert "candidate_coverage" in result.quant_baseline_test_metrics
        assert "candidate_mean_forward_net_bps" in result.quant_baseline_test_metrics
        assert result.train_sessions[-1] < result.validation_sessions[0]
        assert result.validation_sessions[-1] < result.test_sessions[0]
        prediction = LocalModelRegistry(tmp_path).predict_preview(result.model_id,
            _sample_sessions()[date(2026, 10, 8)][:21], market="KR", symbol="005930")
        assert 0 <= prediction["probability_positive"] <= 1
        assert prediction["market"] == "KR"
        assert prediction["approved"] is False
        assert prediction["executable"] is False

        with pytest.raises(ValueError, match="symbol scope"):
            LocalModelRegistry(tmp_path).predict_preview(result.model_id,
                _sample_sessions()[date(2026, 10, 8)][:21], market="KR", symbol="000660")


def test_model_training_removes_first_candidate_if_comparison_fails(tmp_path, monkeypatch) -> None:
    sessions = _sample_sessions()
    registry = LocalModelRegistry(tmp_path)
    original_metrics = ai_pipeline._metrics
    calls = 0

    def fail_while_evaluating_second_model(model, samples):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("second model validation failed")
        return original_metrics(model, samples)

    monkeypatch.setattr(ai_pipeline, "_metrics", fail_while_evaluating_second_model)
    with pytest.raises(RuntimeError, match="second model validation failed"):
        registry.train_compare(
            market="KR", symbol="005930", sessions=sessions,
            label_horizon_bars=5, label_round_trip_cost_bps=Decimal("20"),
            dataset_sha256="a" * 64, dataset_provenance=_provenance("a" * 64),
        )

    assert list(tmp_path.glob("*.joblib")) == []
    assert list(tmp_path.glob("lightgbm-*.json")) == []
    assert registry.list_models() == []


def test_model_prediction_rejects_market_scope_mismatch(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "c" * 32
    _write_candidate(registry, model_id)
    with pytest.raises(ValueError, match="market scope"):
        registry.predict_preview(
            model_id, _sample_sessions()[date(2026, 10, 8)][:21], market="US", symbol="005930",
        )


def test_training_rejects_malformed_dataset_digest_before_training(tmp_path) -> None:
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        LocalModelRegistry(tmp_path).train_compare(
            market="KR", symbol="005930", sessions={},
            label_horizon_bars=5, label_round_trip_cost_bps=Decimal("20"),
            dataset_sha256="not-a-digest", dataset_provenance={},
        )


def test_training_rejects_label_cost_below_modeled_round_trip_cost(tmp_path) -> None:
    with pytest.raises(ValueError, match="must cover modeled buy/sell fees, tax, and slippage"):
        LocalModelRegistry(tmp_path).train_compare(
            market="KR", symbol="005930", sessions={},
            label_horizon_bars=5, label_round_trip_cost_bps=Decimal("19.999"),
            dataset_sha256="a" * 64, dataset_provenance=_provenance("a" * 64),
        )


def test_model_registry_rejects_path_traversal(tmp_path) -> None:
    with pytest.raises(ValueError, match="identifier"):
        LocalModelRegistry(tmp_path).manifest("../../secret")


def test_active_model_selection_fails_closed_for_corrupt_selection_file(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    registry._active_path.write_text("[]", encoding="utf-8")
    assert registry.active_approved_model("KR", "005930") is None


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

    assert registry.active_approved_model("KR", "005930") is None
    listed = next(item for item in registry.list_models() if item["model_id"] == model_id)
    assert listed["feature_compatible"] is False
    assert listed["label_compatible"] is True
    assert listed["model_compatible"] is False
    with pytest.raises(ValueError, match="feature or label schema is outdated"):
        registry.approve(model_id, approved_by="operator", note="Reviewed for migration but requires retraining first.")


def test_model_with_old_execution_label_cannot_be_approved_or_selected(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "d" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata["training_config"]["label_version"] = LABEL_VERSION - 1
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    registry._active_path.write_text(json.dumps({"model_id": model_id}), encoding="utf-8")

    assert registry.active_approved_model("KR", "005930") is None
    listed = next(item for item in registry.list_models() if item["model_id"] == model_id)
    assert listed["feature_compatible"] is True
    assert listed["label_compatible"] is False
    assert listed["model_compatible"] is False
    with pytest.raises(ValueError, match="feature or label schema is outdated"):
        registry.approve(model_id, approved_by="operator", note="Execution aligned labels require a newly trained model.")


def _record_holdout_audit_event(registry: LocalModelRegistry, metadata: dict) -> None:
    evaluation = metadata["heldout_evaluation"]
    registry._record_governance_event({
        "action": "evaluate_holdout", "model_id": metadata["model_id"],
        "market": evaluation["market"], "symbol": evaluation["symbol"],
        "heldout_evaluation": evaluation,
        "artifact_sha256": metadata["artifact_sha256"],
        "threshold": metadata["threshold"],
    })


def _write_candidate(registry: LocalModelRegistry, model_id: str, *, record_holdout_event: bool = True,
                     market: str = "KR", symbol: str = "005930") -> None:
    dataset_sha256 = "e" * 64
    artifact_name = f"{model_id}.joblib"
    artifact = registry.root / artifact_name
    artifact.write_bytes(model_id.encode())
    metadata = {
        "model_id": model_id, "market": market, "model_type": model_id.split("-")[0], "artifact": artifact_name,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "approved": False,
        "feature_names": list(FEATURE_NAMES), "feature_version": FEATURE_VERSION, "threshold": 0.5,
        "validation_metrics": {"accuracy": 0.6, "brier": 0.25}, "test_metrics": {"accuracy": 0.55, "brier": 0.3},
        "quant_baseline_test_metrics": {"accuracy": 0.5, "brier": 0.25},
        "quant_backtest_test_metrics": {"session_count": 5.0, "trade_count": 5.0, "net_pnl": 5.0},
        "ai_quant_backtest_test_metrics": {"session_count": 5.0, "trade_count": 6.0, "net_pnl": 12.0},
        "training_config": {
            "symbol": symbol, "label_version": LABEL_VERSION,
            "label_horizon_bars": 5, "label_round_trip_cost_bps": "20",
            "backtest_quantity_per_session": 1,
            "backtest_buy_commission_bps": "5", "backtest_sell_commission_bps": "5",
            "backtest_sell_tax_bps": "0",
            "backtest_slippage_bps": "5", "backtest_version": BACKTEST_VERSION,
            "backtest_spread_model": BACKTEST_SPREAD_MODEL,
            "backtest_minimum_turnover": "1000000", "backtest_maximum_spread_bps": "15",
            "backtest_minimum_relative_volume": "1.5", "backtest_atr_stop_multiple": "1.5",
            "backtest_reward_risk_multiple": "2",
        },
        "train_sessions": ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"],
        "validation_sessions": ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10"],
        "test_sessions": ["2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"],
        "dataset_sha256": dataset_sha256,
        "data_provenance": _provenance(dataset_sha256),
        "heldout_evaluation": {
            "market": market, "symbol": symbol,
            "candidate_selection": {
                "model_id": model_id, "selection_method": "lowest_validation_brier",
                "validation_brier": 0.25, "validation_session_count": 5,
                "eligible_candidate_count": 1,
            },
            "dataset_sha256": dataset_sha256,
            "data_provenance": _provenance(dataset_sha256),
            "sessions": ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"],
            "metrics": {"accuracy": 0.58, "brier": 0.22},
            "quant_baseline_metrics": {"accuracy": 0.5, "brier": 0.25},
            "quant_backtest_metrics": {"session_count": 5.0, "trade_count": 5.0, "net_pnl": 5.0},
            "ai_quant_backtest_metrics": {"session_count": 5.0, "trade_count": 6.0, "net_pnl": 12.0},
            "model_artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "threshold": 0.5,
            "evaluated_at": "2026-10-20T12:00:00+09:00",
        },
    }
    (registry.root / f"{model_id}.json").write_text(json.dumps(metadata), encoding="utf-8")
    if record_holdout_event:
        _record_holdout_audit_event(registry, metadata)


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
    assert registry.active_approved_model("KR", "005930") is None


def test_model_list_distinguishes_internal_test_from_verified_holdout(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    verified_id = "lightgbm-" + "1" * 32
    unverified_id = "xgboost-" + "2" * 32
    not_run_id = "lightgbm-" + "3" * 32
    _write_candidate(registry, verified_id)
    _write_candidate(registry, unverified_id, record_holdout_event=False)
    _write_candidate(registry, not_run_id, record_holdout_event=False)
    not_run_path = tmp_path / f"{not_run_id}.json"
    not_run = json.loads(not_run_path.read_text(encoding="utf-8"))
    not_run.pop("heldout_evaluation")
    not_run_path.write_text(json.dumps(not_run), encoding="utf-8")

    listed = {item["model_id"]: item for item in registry.list_models()}
    statuses = {model_id: item["holdout_evaluation_status"] for model_id, item in listed.items()}

    assert statuses == {
        verified_id: "VERIFIED",
        unverified_id: "UNVERIFIED",
        not_run_id: "NOT_RUN",
    }
    assert listed[verified_id]["test_sessions"] == [
        "2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15",
    ]
    assert listed[verified_id]["heldout_sessions"] == [
        "2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20",
    ]
    assert listed[verified_id]["test_metrics"]["brier"] == 0.3
    assert listed[verified_id]["heldout_metrics"]["brier"] == 0.22


def test_model_with_previous_backtest_version_cannot_be_approved_or_selected(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "d" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata["training_config"]["backtest_version"] = BACKTEST_VERSION - 1
    metadata["approved"] = True
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    registry._active_path.write_text(json.dumps({"model_id": model_id}), encoding="utf-8")

    assert registry.active_approved_model("KR", "005930") is None
    listed = next(item for item in registry.list_models() if item["model_id"] == model_id)
    assert listed["approved"] is False
    assert listed["approval_evidence_verified"] is False
    with pytest.raises(ValueError, match="metrics or training configuration are inconsistent"):
        registry.approve(model_id, approved_by="operator", note="Older spread assumptions require model retraining.")


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
    approval_event = next(event for event in registry.governance_events() if event["action"] == "approve")
    assert len(approval_event["test_sessions"]) == 5
    assert approval_event["heldout_ai_quant_backtest_metrics"]["net_pnl"] == 12.0
    assert approval_event["training_config"]["backtest_buy_commission_bps"] == "5"
    assert registry.governance_events()[-1]["action"] == "rollback"
    assert registry.active_approved_model("KR", "005930")["model_id"] == first
    assert registry.active_approved_model("KR", "000660") is None


def test_us_model_governance_is_scoped_to_us_symbol(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "c" * 32
    _write_candidate(registry, model_id, market="US", symbol="AAPL")
    registry.approve(model_id, approved_by="operator", note="Independent holdout evidence reviewed for US scope.")
    registry.activate(model_id, changed_by="operator", note="Activate the reviewed US model only in its scope.")

    assert registry.active_approved_model("US", "AAPL")["model_id"] == model_id
    assert registry.active_approved_model("US", "MSFT") is None
    assert registry.active_approved_model("KR", "005930") is None


def test_active_kr_model_registry_keeps_symbol_selections_independent(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    domestic_samsung = "lightgbm-" + "1" * 32
    domestic_sk = "xgboost-" + "2" * 32
    _write_candidate(registry, domestic_samsung)
    _write_candidate(registry, domestic_sk, symbol="000660")
    for model_id in (domestic_samsung, domestic_sk):
        registry.approve(model_id, approved_by="operator", note="Independent holdout evidence reviewed for scoped activation.")

    # Migrate an existing single-pointer KR selection while adding independent scopes.
    registry._active_path.write_text(json.dumps({"model_id": domestic_samsung,
                                                 "changed_at": "2026-10-20T12:00:00+09:00"}), encoding="utf-8")
    registry.activate(domestic_sk, changed_by="operator", note="Select the independently evaluated SK model.")

    assert registry.active_approved_model("KR", "005930")["model_id"] == domestic_samsung
    assert registry.active_approved_model("KR", "000660")["model_id"] == domestic_sk
    active_ids = {item["model_id"] for item in registry.list_models() if item.get("active")}
    assert active_ids == {domestic_samsung, domestic_sk}
    assert set(json.loads(registry._active_models_path.read_text(encoding="utf-8"))["models"]) == {
        "KR:005930", "KR:000660",
    }

    registry._active_models_path.write_text(json.dumps({"schema_version": 1, "models": {
        "KR:005930": {"model_id": "malformed"},
    }}), encoding="utf-8")
    assert registry.active_approved_model("KR", "005930") is None

    registry._active_models_path.write_text(json.dumps({"schema_version": 1, "models": {
        "KR:005930": {"model_id": domestic_sk},
    }}), encoding="utf-8")
    assert registry.active_approved_model("KR", "005930") is None


def test_us_active_model_registry_selection_is_independent(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "3" * 32
    _write_candidate(registry, model_id, market="US", symbol="AAPL")
    registry.approve(model_id, approved_by="operator", note="Independent holdout evidence reviewed for US scope.")
    registry.activate(model_id, changed_by="operator", note="Activate the reviewed US model in its own scope.")

    assert registry.active_approved_model("US", "AAPL")["model_id"] == model_id
    assert registry.active_approved_model("US", "MSFT") is None
    assert registry.active_approved_model("KR", "005930") is None


def test_model_approval_requires_sufficient_heldout_sessions_and_ai_quant_outperformance(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "9" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata["heldout_evaluation"]["ai_quant_backtest_metrics"]["net_pnl"] = 4.0
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="must be positive and exceed the quant-only baseline"):
        registry.approve(model_id, approved_by="operator", note="Review held-out execution comparison before promotion.")

    metadata["approved"] = True
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    registry._active_path.write_text(json.dumps({"model_id": model_id}), encoding="utf-8")
    assert registry.active_approved_model("KR", "005930") is None
    listed = next(item for item in registry.list_models() if item["model_id"] == model_id)
    assert listed["approved"] is False
    assert listed["approval_evidence_verified"] is False
    with pytest.raises(ValueError, match="valid held-out comparative strategy evidence"):
        registry.activate(model_id, changed_by="operator", note="Do not activate without reliable held-out evidence.")

    metadata["heldout_evaluation"]["ai_quant_backtest_metrics"]["net_pnl"] = 12.0
    metadata["approved"] = False
    metadata["heldout_evaluation"]["sessions"] = ["2026-10-16"]
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="at least five distinct independent holdout sessions"):
        registry.approve(model_id, approved_by="operator", note="Review held-out execution comparison before promotion.")

    metadata["heldout_evaluation"]["sessions"] = ["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"]
    metadata["validation_sessions"][-1] = "2026-10-11"
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="disjoint and strictly chronological"):
        registry.approve(model_id, approved_by="operator", note="Review held-out execution comparison before promotion.")

    metadata["validation_sessions"][-1] = "2026-10-10"
    metadata["heldout_evaluation"]["ai_quant_backtest_metrics"]["session_count"] = 4
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="metrics or training configuration are inconsistent"):
        registry.approve(model_id, approved_by="operator", note="Review held-out execution comparison before promotion.")


def test_model_approval_requires_matching_durable_holdout_evaluation(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "8" * 32
    _write_candidate(registry, model_id, record_holdout_event=False)

    with pytest.raises(ValueError, match="matching durable evaluation audit event"):
        registry.approve(model_id, approved_by="operator", note="Require an auditable independent evaluation.")
    assert registry.manifest(model_id)["approved"] is False


def test_holdout_dataset_hash_cannot_be_changed_after_evaluation(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "9" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed_dataset_sha256 = "f" * 64
    metadata["heldout_evaluation"]["dataset_sha256"] = changed_dataset_sha256
    metadata["heldout_evaluation"]["data_provenance"] = _provenance(changed_dataset_sha256)
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")

    assert registry._has_valid_approval_evidence(metadata) is True
    assert registry._has_durable_holdout_evaluation(metadata) is False
    with pytest.raises(ValueError, match="matching durable evaluation audit event"):
        registry.approve(model_id, approved_by="operator", note="Reject altered holdout dataset evidence.")


def test_model_approval_rejects_holdout_evidence_for_a_different_artifact(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "8" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata["heldout_evaluation"]["model_artifact_sha256"] = "0" * 64

    assert registry._has_valid_approval_evidence(metadata) is False
    metadata["approved"] = True
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert registry.active_approved_model("KR", "005930") is None


def test_candidate_cannot_be_re_evaluated_on_multiple_holdout_sets(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "2" * 32
    _write_candidate(registry, model_id)

    with pytest.raises(ValueError, match="already has independent holdout evidence"):
        registry.evaluate_candidate(
            model_id, {}, market="KR", symbol="005930", dataset_sha256="f" * 64,
            dataset_provenance=_provenance("f" * 64),
        )


def test_holdout_selection_uses_validation_brier_and_one_prior_champion(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    candidates = (
        ("lightgbm-" + "a" * 32, 0.05),
        ("xgboost-" + "b" * 32, 0.24),
        ("lightgbm-" + "c" * 32, 0.30),
    )
    for index, (model_id, brier) in enumerate(candidates):
        _write_candidate(registry, model_id, record_holdout_event=False)
        path = tmp_path / f"{model_id}.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata.pop("heldout_evaluation")
        metadata["validation_metrics"]["brier"] = brier
        if index == 0:
            metadata["train_sessions"] = [f"2026-09-{day:02d}" for day in range(29, 31)] + [
                "2026-10-01", "2026-10-02", "2026-10-03",
            ]
            metadata["validation_sessions"] = ["2026-10-04", "2026-10-05", "2026-10-06"]
            metadata["test_sessions"] = ["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10", "2026-10-11"]
        path.write_text(json.dumps(metadata), encoding="utf-8")

    holdout_dates = [date(2026, 10, day) for day in (16, 19, 20, 21, 22)]
    selection = registry.select_candidate_for_holdout(
        "KR", "005930", {session: [] for session in holdout_dates},
    )

    assert selection == {
        "model_id": candidates[1][0], "selection_method": "lowest_validation_brier",
        "validation_brier": 0.24, "validation_session_count": 5,
        "eligible_candidate_count": 2,
    }
    with pytest.raises(ValueError, match="only the validation-selected candidate"):
        registry.evaluate_candidate(
            candidates[0][0], {session: [] for session in holdout_dates},
            market="KR", symbol="005930", dataset_sha256="f" * 64,
            dataset_provenance=_provenance("f" * 64),
        )


def test_holdout_selection_rejects_ambiguous_or_datetime_session_keys(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    session = date(2026, 10, 16)

    with pytest.raises(ValueError, match="session keys must be calendar dates"):
        registry.select_candidate_for_holdout("KR", "005930", {session: [], "2026-10-19": []})
    with pytest.raises(ValueError, match="session keys must be calendar dates"):
        registry.select_candidate_for_holdout("KR", "005930", {datetime(2026, 10, 16): []})


def test_holdout_selection_rejects_incomparable_latest_training_cohorts(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    candidate_ids = ("lightgbm-" + "1" * 32, "xgboost-" + "2" * 32)
    for model_id in candidate_ids:
        _write_candidate(registry, model_id, record_holdout_event=False)
        path = tmp_path / f"{model_id}.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata.pop("heldout_evaluation")
        metadata["validation_metrics"]["brier"] = 0.2
        path.write_text(json.dumps(metadata), encoding="utf-8")
    conflicting_path = tmp_path / f"{candidate_ids[1]}.json"
    conflicting = json.loads(conflicting_path.read_text(encoding="utf-8"))
    conflicting["dataset_sha256"] = "f" * 64
    conflicting["data_provenance"] = _provenance("f" * 64)
    conflicting_path.write_text(json.dumps(conflicting), encoding="utf-8")

    holdout_dates = [date(2026, 10, day) for day in (16, 19, 20, 21, 22)]
    with pytest.raises(ValueError, match="multiple candidate training cohorts"):
        registry.select_candidate_for_holdout(
            "KR", "005930", {session: [] for session in holdout_dates},
        )


def test_holdout_selection_rejects_short_validation_history(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "d" * 32
    _write_candidate(registry, model_id, record_holdout_event=False)
    path = tmp_path / f"{model_id}.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata.pop("heldout_evaluation")
    metadata["validation_sessions"] = ["2026-10-06", "2026-10-07"]
    metadata["validation_metrics"]["brier"] = 0.2
    path.write_text(json.dumps(metadata), encoding="utf-8")

    holdout_dates = [date(2026, 10, day) for day in (16, 19, 20, 21, 22)]
    with pytest.raises(ValueError, match="three distinct prior validation sessions"):
        registry.select_candidate_for_holdout(
            "KR", "005930", {session: [] for session in holdout_dates},
        )


def test_holdout_sessions_cannot_be_reused_for_another_candidate(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    prior_id = "lightgbm-" + "e" * 32
    next_id = "xgboost-" + "f" * 32
    prior_holdout = [date(2026, 10, day) for day in (16, 19, 20, 21, 22)]
    _write_candidate(registry, prior_id, record_holdout_event=False)
    prior_path = tmp_path / f"{prior_id}.json"
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    prior["heldout_evaluation"].update({
        "market": "KR", "symbol": "005930", "sessions": [day.isoformat() for day in prior_holdout],
        "dataset_sha256": "f" * 64,
        "data_provenance": _provenance("f" * 64),
    })
    prior_path.write_text(json.dumps(prior), encoding="utf-8")
    _record_holdout_audit_event(registry, prior)

    _write_candidate(registry, next_id, record_holdout_event=False)
    next_path = tmp_path / f"{next_id}.json"
    next_metadata = json.loads(next_path.read_text(encoding="utf-8"))
    next_metadata.pop("heldout_evaluation")
    next_path.write_text(json.dumps(next_metadata), encoding="utf-8")
    overlapping = [date(2026, 10, day) for day in (20, 21, 22, 23, 26)]

    with pytest.raises(ValueError, match="already been used for this market and symbol"):
        registry.evaluate_candidate(
            next_id, {session: [] for session in overlapping}, market="KR", symbol="005930",
            dataset_sha256="a" * 64, dataset_provenance=_provenance("a" * 64),
        )


def test_holdout_evaluation_rolls_back_manifest_when_audit_write_fails_and_allows_retry(
    tmp_path, monkeypatch,
) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "3" * 32
    _write_candidate(registry, model_id, record_holdout_event=False)
    manifest_path = tmp_path / f"{model_id}.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    original.pop("heldout_evaluation")
    manifest_path.write_text(json.dumps(original), encoding="utf-8")

    heldout_dates = [date(2026, 10, day) for day in (16, 19, 20, 21, 22)]
    sessions = {session: [] for session in heldout_dates}
    monkeypatch.setattr(ai_pipeline, "build_training_samples", lambda *_args, **_kwargs: [
        SimpleNamespace(session=session) for session in heldout_dates
    ])
    monkeypatch.setattr(ai_pipeline, "_metrics", lambda *_args, **_kwargs: {"accuracy": 0.6})
    monkeypatch.setattr(ai_pipeline, "_quant_baseline_metrics", lambda *_args, **_kwargs: {"accuracy": 0.5})
    monkeypatch.setattr(ai_pipeline, "_backtest_metrics", lambda _results: {
        "session_count": 5.0, "signal_count": 0.0, "trade_count": 0.0,
        "winning_trades": 0.0, "win_rate": 0.0, "gross_pnl": "0",
        "estimated_costs": "0", "net_pnl": "0", "average_trade_net_pnl": "0",
    })
    monkeypatch.setattr(ai_pipeline, "backtest_orb_vwap", lambda *_args, **_kwargs: object())
    import joblib
    monkeypatch.setattr(joblib, "load", lambda _path: object())

    record_event = registry._record_governance_event
    monkeypatch.setattr(registry, "_record_governance_event", lambda _event: (_ for _ in ()).throw(
        OSError("simulated audit storage failure")
    ))
    with pytest.raises(OSError, match="simulated audit storage failure"):
        registry.evaluate_candidate(
            model_id, sessions, market="KR", symbol="005930", dataset_sha256="f" * 64,
            dataset_provenance=_provenance("f" * 64),
        )

    assert registry.manifest(model_id) == original
    assert registry.governance_audit_status() == {"integrity_verified": True, "event_count": 0}

    monkeypatch.setattr(registry, "_record_governance_event", record_event)
    result = registry.evaluate_candidate(
        model_id, sessions, market="KR", symbol="005930", dataset_sha256="f" * 64,
        dataset_provenance=_provenance("f" * 64),
    )
    assert result["heldout_sessions"] == [session.isoformat() for session in heldout_dates]
    evaluated = registry.manifest(model_id)
    assert evaluated["heldout_evaluation"]["sessions"] == result["heldout_sessions"]
    assert evaluated["test_sessions"] == original["test_sessions"]
    assert evaluated["test_metrics"] == original["test_metrics"]
    assert evaluated["quant_baseline_test_metrics"] == original["quant_baseline_test_metrics"]
    assert evaluated["quant_backtest_test_metrics"] == original["quant_backtest_test_metrics"]
    assert evaluated["ai_quant_backtest_test_metrics"] == original["ai_quant_backtest_test_metrics"]
    assert evaluated["heldout_evaluation"]["metrics"] == result["heldout_metrics"]
    assert registry.governance_audit_status() == {"integrity_verified": True, "event_count": 1}


def test_governance_audit_replace_failure_preserves_previous_log(tmp_path, monkeypatch) -> None:
    registry = LocalModelRegistry(tmp_path)
    registry._record_governance_event({"action": "baseline", "model_id": "lightgbm-" + "4" * 32})
    prior_bytes = registry._audit_path.read_bytes()
    original_replace = ai_pipeline.os.replace

    def fail_audit_replace(source, destination):
        if destination == registry._audit_path:
            raise OSError("simulated atomic audit replacement failure")
        return original_replace(source, destination)

    monkeypatch.setattr(ai_pipeline.os, "replace", fail_audit_replace)
    with pytest.raises(OSError, match="atomic audit replacement failure"):
        registry._record_governance_event({"action": "next", "model_id": "lightgbm-" + "5" * 32})

    assert registry._audit_path.read_bytes() == prior_bytes
    assert registry.governance_audit_status() == {"integrity_verified": True, "event_count": 1}
    assert list(tmp_path.glob(".governance-audit-*.tmp")) == []


def test_model_approval_rejects_label_cost_below_backtest_round_trip_cost(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "6" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata["training_config"]["label_round_trip_cost_bps"] = "19.999"
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")

    assert registry._has_valid_approval_evidence(metadata) is False
    with pytest.raises(ValueError, match="metrics or training configuration are inconsistent"):
        registry.approve(model_id, approved_by="operator", note="Held-out evidence cost assumptions need review.")


def test_model_approval_rejects_zero_cost_backtests(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "xgboost-" + "5" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata["training_config"]["label_round_trip_cost_bps"] = "0"
    metadata["training_config"]["backtest_buy_commission_bps"] = "0"
    metadata["training_config"]["backtest_sell_commission_bps"] = "0"
    metadata["training_config"]["backtest_sell_tax_bps"] = "0"
    metadata["training_config"]["backtest_slippage_bps"] = "0"
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")

    assert registry._has_valid_approval_evidence(metadata) is False
    with pytest.raises(ValueError, match="metrics or training configuration are inconsistent"):
        registry.approve(model_id, approved_by="operator", note="Zero-cost evidence cannot approve a trading model.")


@pytest.mark.parametrize("trade_count", [4, 4.9, 5.5, True, float("nan"), float("inf")])
def test_holdout_approval_criteria_rejects_invalid_trade_counts(trade_count) -> None:
    ai_metrics = {"trade_count": trade_count, "net_pnl": "12"}
    quant_metrics = {"net_pnl": "5"}

    assert ai_pipeline._meets_holdout_approval_criteria(ai_metrics, quant_metrics) is False


def test_holdout_approval_criteria_accepts_five_finite_integer_trades() -> None:
    assert ai_pipeline._meets_holdout_approval_criteria(
        {"trade_count": 5.0, "net_pnl": "12"}, {"net_pnl": "5"}
    ) is True


@pytest.mark.parametrize("trade_count", [5.5, True, float("nan")])
def test_model_approval_rejects_noninteger_ai_trade_count(tmp_path, trade_count) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "8" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata["heldout_evaluation"]["ai_quant_backtest_metrics"]["trade_count"] = trade_count
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="trade count must be a finite integer"):
        registry.approve(model_id, approved_by="operator", note="Reject an invalid held-out trade count safely.")


def test_model_approval_enforces_five_ai_quant_trades_as_the_exact_minimum(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "7" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))

    metadata["heldout_evaluation"]["ai_quant_backtest_metrics"]["trade_count"] = 4
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="at least five held-out AI\\+quant strategy trades"):
        registry.approve(model_id, approved_by="operator", note="Held-out execution evidence reviewed for approval.")

    metadata["heldout_evaluation"]["ai_quant_backtest_metrics"]["trade_count"] = 5
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    _record_holdout_audit_event(registry, metadata)
    approved = registry.approve(model_id, approved_by="operator", note="Held-out execution evidence reviewed for approval.")
    assert approved["approved"] is True

    metadata["heldout_evaluation"]["ai_quant_backtest_metrics"]["session_count"] = 5
    metadata["training_config"].pop("backtest_slippage_bps")
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="metrics or training configuration are inconsistent"):
        registry.approve(model_id, approved_by="operator", note="Review held-out execution comparison before promotion.")


@pytest.mark.parametrize("failure", [OSError("simulated audit storage failure"), ValueError("simulated audit chain failure")])
def test_model_governance_rolls_back_state_when_audit_write_fails(tmp_path, monkeypatch, failure) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "c" * 32
    _write_candidate(registry, model_id)
    record_event = registry._record_governance_event

    def fail_audit(_event):
        raise failure

    monkeypatch.setattr(registry, "_record_governance_event", fail_audit)
    with pytest.raises(type(failure), match="simulated audit"):
        registry.approve(model_id, approved_by="operator", note="Held-out test evidence reviewed for advisory use.")
    assert registry.manifest(model_id)["approved"] is False

    monkeypatch.setattr(registry, "_record_governance_event", record_event)
    registry.approve(model_id, approved_by="operator", note="Held-out test evidence reviewed for advisory use.")
    registry.activate(model_id, changed_by="operator", note="Approved candidate selected for advisory use only.")
    replacement_id = "xgboost-" + "d" * 32
    _write_candidate(registry, replacement_id)
    registry.approve(replacement_id, approved_by="operator", note="Held-out test evidence reviewed for advisory use.")

    monkeypatch.setattr(registry, "_record_governance_event", fail_audit)
    with pytest.raises(type(failure), match="simulated audit"):
        registry.activate(replacement_id, changed_by="operator", note="Candidate change requires a durable audit event.")
    assert registry.active_approved_model("KR", "005930")["model_id"] == model_id


def test_approved_manifest_without_durable_audit_event_cannot_be_selected_or_activated(tmp_path, monkeypatch) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "7" * 32
    _write_candidate(registry, model_id)
    record_event = registry._record_governance_event
    monkeypatch.setattr(registry, "_record_governance_event", lambda _event: None)

    registry.approve(model_id, approved_by="operator", note="Reviewed held-out evidence before advisory activation.")

    listed = next(item for item in registry.list_models() if item["model_id"] == model_id)
    assert listed["approved"] is False
    assert listed["approval_audit_verified"] is False
    assert registry.active_approved_model("KR", "005930") is None
    with pytest.raises(ValueError, match="durable matching governance audit event"):
        registry.activate(model_id, changed_by="operator", note="Refuse activation without a durable approval event.")

    monkeypatch.setattr(registry, "_record_governance_event", record_event)
    registry.approve(model_id, approved_by="operator", note="Reviewed held-out evidence before advisory activation.")
    registry.activate(model_id, changed_by="operator", note="Durable approval evidence verified for advisory activation.")
    assert registry.active_approved_model("KR", "005930")["model_id"] == model_id

    events = registry.governance_events()
    assert len(events) == 3
    assert events[0]["previous_event_sha256"] == "0" * 64
    assert events[1]["previous_event_sha256"] == events[0]["event_sha256"]
    assert events[1]["action"] == "approve"
    assert events[2]["previous_event_sha256"] == events[1]["event_sha256"]
    assert registry.governance_audit_status() == {"integrity_verified": True, "event_count": 3}

    event = events[1]
    event["artifact_sha256"] = "0" * 64
    registry._audit_path.write_text(
        "\n".join(json.dumps(value) for value in [events[0], event, events[2]]) + "\n", encoding="utf-8"
    )
    listed = next(item for item in registry.list_models() if item["model_id"] == model_id)
    assert listed["approved"] is False
    assert listed["approval_audit_verified"] is False
    assert registry.active_approved_model("KR", "005930") is None
    assert registry.governance_events() == []
    assert registry.governance_audit_status() == {"integrity_verified": False, "event_count": 0}

    registry._audit_path.write_text(json.dumps(events[1]) + "\n", encoding="utf-8")
    assert registry.governance_audit_status() == {"integrity_verified": False, "event_count": 0}

    blocked_id = "xgboost-" + "e" * 32
    _write_candidate(registry, blocked_id, record_holdout_event=False)
    with pytest.raises(ValueError, match="matching durable evaluation audit event"):
        registry.approve(blocked_id, approved_by="operator", note="Approval must stop when prior audit history is damaged.")
    assert registry.manifest(blocked_id)["approved"] is False


def test_approved_model_threshold_is_bound_to_holdout_and_audit_evidence(tmp_path) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "a" * 32
    _write_candidate(registry, model_id)
    registry.approve(model_id, approved_by="operator", note="Held-out evidence reviewed for advisory use.")
    registry.activate(model_id, changed_by="operator", note="Approved model selected for advisory use.")
    assert registry.active_approved_model("KR", "005930")["model_id"] == model_id

    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata["threshold"] = 0.51
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")

    listed = next(item for item in registry.list_models() if item["model_id"] == model_id)
    assert listed["approved"] is False
    assert listed["approval_audit_verified"] is False
    assert registry.active_approved_model("KR", "005930") is None


@pytest.mark.parametrize("threshold", [True, -0.01, 1.01, float("nan"), "0.5"])
def test_approval_rejects_invalid_decision_threshold(tmp_path, threshold) -> None:
    registry = LocalModelRegistry(tmp_path)
    model_id = "lightgbm-" + "b" * 32
    _write_candidate(registry, model_id)
    manifest_path = tmp_path / f"{model_id}.json"
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata["threshold"] = threshold
    metadata["heldout_evaluation"]["threshold"] = threshold
    manifest_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match=r"threshold must be finite and within \[0, 1\]"):
        registry.approve(model_id, approved_by="operator", note="Decision threshold requires a valid range.")
