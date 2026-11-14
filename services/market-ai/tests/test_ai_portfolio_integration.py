import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import numpy as np

from app.ai_pipeline import LocalModelRegistry
from app.quant import MinuteBar


def _bars() -> list[MinuteBar]:
    start = datetime(2026, 11, 5, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    return [MinuteBar(start + timedelta(minutes=index), Decimal("100"), Decimal("101"), Decimal("99"),
                      Decimal("100"), 1000, Decimal("5"), start + timedelta(minutes=index, seconds=30))
            for index in range(30)]


def test_active_approved_entry_filter_loads_once_and_uses_model_threshold(tmp_path, monkeypatch) -> None:
    model_id = "lightgbm-" + "a" * 32
    metadata = {
        "model_id": model_id,
        "market": "KR",
        "artifact": model_id + ".joblib",
        "artifact_sha256": "b" * 64,
        "threshold": 0.7,
        "training_config": {"symbol": "005930", "backtest_opening_range_bars": 5},
    }
    class Model:
        calls = 0
        classes_ = [0, 1]

        def predict_proba(self, _features):
            self.calls += 1
            return np.asarray([[0.4, 0.6]], dtype=np.float32)

    model = Model()
    load_calls = []
    registry = LocalModelRegistry(tmp_path)
    monkeypatch.setattr(registry, "active_approved_model", lambda market, symbol: metadata)
    monkeypatch.setattr(registry, "manifest", lambda requested_id: metadata)
    monkeypatch.setitem(sys.modules, "joblib", SimpleNamespace(load=lambda path: load_calls.append(path) or model))

    active = registry.active_approved_entry_filter("KR", "005930")

    assert active is not None
    selected_id, entry_filter = active
    assert selected_id == model_id
    assert entry_filter(_bars()[:21]) is False
    assert entry_filter(_bars()[:22]) is False
    assert len(load_calls) == 1
    assert model.calls == 2


def test_missing_active_model_fails_closed_without_loading_artifact(tmp_path, monkeypatch) -> None:
    registry = LocalModelRegistry(tmp_path)
    monkeypatch.setattr(registry, "active_approved_model", lambda _market, _symbol: None)

    assert registry.active_approved_entry_filter("KR", "005930") is None


def test_active_model_rejects_invalid_probability_distribution(tmp_path, monkeypatch) -> None:
    model_id = "xgboost-" + "c" * 32
    metadata = {
        "model_id": model_id,
        "market": "KR",
        "artifact": model_id + ".joblib",
        "artifact_sha256": "d" * 64,
        "threshold": 0.5,
        "training_config": {"symbol": "005930", "backtest_opening_range_bars": 5},
    }
    model = SimpleNamespace(classes_=[0, 1],
                            predict_proba=lambda _features: np.asarray([[0.2, 0.9]], dtype=np.float32))
    registry = LocalModelRegistry(tmp_path)
    monkeypatch.setattr(registry, "active_approved_model", lambda _market, _symbol: metadata)
    monkeypatch.setattr(registry, "manifest", lambda _model_id: metadata)
    monkeypatch.setitem(sys.modules, "joblib", SimpleNamespace(load=lambda _path: model))
    active = registry.active_approved_entry_filter("KR", "005930")

    assert active is not None
    try:
        active[1](_bars()[:21])
    except ValueError as exc:
        assert "invalid binary-class probabilities" in str(exc)
    else:
        raise AssertionError("invalid model probabilities must fail closed")


def test_model_backtest_dates_must_follow_training_validation_test_and_holdout(tmp_path, monkeypatch) -> None:
    model_id = "lightgbm-" + "e" * 32
    metadata = {
        "train_sessions": ["2026-11-02"],
        "validation_sessions": ["2026-11-03"],
        "test_sessions": ["2026-11-04"],
        "heldout_evaluation": {"sessions": ["2026-11-05"]},
    }
    registry = LocalModelRegistry(tmp_path)
    monkeypatch.setattr(registry, "manifest", lambda _model_id: metadata)

    assert registry.validate_model_backtest_sessions(model_id, {date(2026, 11, 6)}) == metadata
    try:
        registry.validate_model_backtest_sessions(model_id, {date(2026, 11, 5)})
    except ValueError as exc:
        assert "strictly later" in str(exc)
    else:
        raise AssertionError("overlapping holdout sessions must be rejected")
