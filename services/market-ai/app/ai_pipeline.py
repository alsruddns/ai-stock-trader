"""Leakage-aware LightGBM/XGBoost training and local model registry."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from .quant import MinuteBar, _atr, _ema, _vwap

FEATURE_NAMES = (
    "return_1m", "return_3m", "return_5m", "close_vwap_pct", "ema9_gap_pct",
    "ema20_gap_pct", "atr14_pct", "relative_volume20", "spread_bps", "session_progress",
)


def _session_progress(timestamp) -> Decimal:
    """Fraction of a conventional 6.5-hour regular session elapsed, using only wall time."""
    seconds = timestamp.hour * 3600 + timestamp.minute * 60 + timestamp.second
    return min(Decimal(1), Decimal(seconds) / Decimal(6.5 * 60 * 60))


@dataclass(frozen=True)
class TrainingSample:
    session: date
    timestamp: str
    features: tuple[float, ...]
    label: int


@dataclass(frozen=True)
class ModelTrainingResult:
    model_id: str
    model_type: str
    threshold: float
    validation_metrics: dict[str, float]
    test_metrics: dict[str, float]
    train_sessions: tuple[str, ...]
    validation_sessions: tuple[str, ...]
    test_sessions: tuple[str, ...]
    artifact_sha256: str


def build_training_samples(
    sessions: dict[date, list[MinuteBar]], *, horizon_bars: int = 5,
    round_trip_cost_bps: Decimal = Decimal("10"), warmup_bars: int = 20,
) -> list[TrainingSample]:
    """Create samples using features available at t and a label from t+horizon.

    Sessions are processed independently, so labels never cross a close. The label
    is positive only when forward close-to-close return exceeds estimated round-trip
    costs. Caller owns data licensing and corporate-action/symbol normalization.
    """
    if horizon_bars <= 0 or warmup_bars <= 0 or round_trip_cost_bps < 0:
        raise ValueError("horizon, warmup, and transaction cost settings are invalid")
    samples: list[TrainingSample] = []
    required = max(warmup_bars, 20, 14, 9)
    for session, bars in sorted(sessions.items()):
        if not bars:
            continue
        if any(item.timestamp.date() != session for item in bars):
            raise ValueError("bar date does not match its session key")
        if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:])):
            raise ValueError("session bars must be strictly increasing")
        for index in range(required - 1, len(bars) - horizon_bars):
            current = bars[index]
            history = bars[: index + 1]
            prior = bars[max(0, index - 19):index]
            mean_volume = Decimal(sum(item.volume for item in prior)) / len(prior) if prior else Decimal(0)
            volume_ratio = Decimal(current.volume) / mean_volume if mean_volume else Decimal(0)
            if current.spread_bps is None:
                continue
            close = current.close
            returns = [
                Decimal(0) if index < period else close / bars[index - period].close - Decimal(1)
                for period in (1, 3, 5)
            ]
            vwap = _vwap(history)
            ema9 = _ema([item.close for item in history[-9:]], 9)
            ema20 = _ema([item.close for item in history[-20:]], 20)
            atr_pct = _atr(history, 14) / close
            session_progress = _session_progress(current.timestamp)
            feature_values = (
                *returns,
                close / vwap - Decimal(1) if vwap else Decimal(0),
                close / ema9 - Decimal(1),
                close / ema20 - Decimal(1),
                atr_pct,
                volume_ratio,
                current.spread_bps,
                session_progress,
            )
            if any(not value.is_finite() for value in feature_values):
                continue
            future_return = bars[index + horizon_bars].close / close - Decimal(1)
            label = int(future_return > round_trip_cost_bps / Decimal(10_000))
            samples.append(TrainingSample(session, current.timestamp.isoformat(), tuple(float(value) for value in feature_values), label))
    return samples


def latest_features(bars: list[MinuteBar], *, minimum_bars: int = 20) -> tuple[float, ...]:
    """Build the current-bar feature vector without reading a future observation."""
    if len(bars) < minimum_bars:
        raise ValueError(f"at least {minimum_bars} bars are required for inference")
    if any(item.timestamp.date() != bars[0].timestamp.date() for item in bars):
        raise ValueError("inference input must contain one session")
    if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:])):
        raise ValueError("inference bars must be strictly increasing")
    current = bars[-1]
    if current.spread_bps is None:
        raise ValueError("latest bar must include spread")
    index = len(bars) - 1
    prior = bars[max(0, index - 19):index]
    mean_volume = Decimal(sum(item.volume for item in prior)) / len(prior) if prior else Decimal(0)
    volume_ratio = Decimal(current.volume) / mean_volume if mean_volume else Decimal(0)
    close = current.close
    features = (
        *(Decimal(0) if index < period else close / bars[index - period].close - Decimal(1) for period in (1, 3, 5)),
        close / _vwap(bars) - Decimal(1),
        close / _ema([item.close for item in bars[-9:]], 9) - Decimal(1),
        close / _ema([item.close for item in bars[-20:]], 20) - Decimal(1),
        _atr(bars, 14) / close,
        volume_ratio,
        current.spread_bps,
        _session_progress(current.timestamp),
    )
    return tuple(float(value) for value in features)


def _chronological_partitions(samples: list[TrainingSample]) -> tuple[list[TrainingSample], list[TrainingSample], list[TrainingSample]]:
    dates = sorted({sample.session for sample in samples})
    if len(dates) < 3:
        raise ValueError("at least three distinct sessions are required for train/validation/test splits")
    train_end = max(1, math.floor(len(dates) * 0.6))
    validation_end = max(train_end + 1, math.floor(len(dates) * 0.8))
    validation_end = min(validation_end, len(dates) - 1)
    train_dates = set(dates[:train_end])
    validation_dates = set(dates[train_end:validation_end])
    test_dates = set(dates[validation_end:])
    parts = tuple([sample for sample in samples if sample.session in group] for group in (train_dates, validation_dates, test_dates))
    if any(not part for part in parts):
        raise ValueError("chronological train/validation/test partitions must all contain samples")
    return parts  # type: ignore[return-value]


def _metrics(model: Any, samples: list[TrainingSample]) -> dict[str, float]:
    from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score

    x = [sample.features for sample in samples]
    y = [sample.label for sample in samples]
    probabilities = model.predict_proba(x)[:, 1]
    predictions = (probabilities >= 0.5).astype(int)
    metrics = {"accuracy": float(accuracy_score(y, predictions)), "brier": float(brier_score_loss(y, probabilities))}
    metrics["roc_auc"] = float(roc_auc_score(y, probabilities)) if len(set(y)) == 2 else 0.5
    return metrics


class LocalModelRegistry:
    """Stores model artifacts outside source control; newly trained models stay unapproved."""

    def __init__(self, root: str | Path = ".local/models"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def train_compare(self, samples: list[TrainingSample]) -> list[ModelTrainingResult]:
        try:
            from lightgbm import LGBMClassifier
            from xgboost import XGBClassifier
            import joblib
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("Install the optional ai dependencies to train LightGBM and XGBoost models") from exc
        train, validation, test = _chronological_partitions(samples)
        if len(train) < 30 or len(validation) < 10 or len(test) < 10:
            raise ValueError("insufficient samples per chronological partition")
        if len({sample.label for sample in train}) < 2:
            raise ValueError("training partition must contain both outcome classes")
        x_train = np.asarray([sample.features for sample in train], dtype=np.float32)
        y_train = np.asarray([sample.label for sample in train], dtype=np.int32)
        candidates = {
            "lightgbm": LGBMClassifier(n_estimators=80, num_leaves=15, learning_rate=0.05, verbosity=-1, random_state=42, n_jobs=1),
            "xgboost": XGBClassifier(n_estimators=80, max_depth=3, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42, n_jobs=1),
        }
        results: list[ModelTrainingResult] = []
        train_dates = tuple(sorted({sample.session.isoformat() for sample in train}))
        validation_dates = tuple(sorted({sample.session.isoformat() for sample in validation}))
        test_dates = tuple(sorted({sample.session.isoformat() for sample in test}))
        for name, model in candidates.items():
            model.fit(x_train, y_train)
            validation_metrics = _metrics(model, validation)
            test_metrics = _metrics(model, test)
            model_id = f"{name}-{uuid4().hex}"
            artifact_name = f"{model_id}.joblib"
            artifact_path = self.root / artifact_name
            if artifact_path.parent != self.root:
                raise ValueError("invalid model artifact path")
            fd, temporary = tempfile.mkstemp(prefix=".model-", suffix=".tmp", dir=self.root)
            os.close(fd)
            try:
                joblib.dump(model, temporary)
                os.replace(temporary, artifact_path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            manifest = {
                "model_id": model_id, "model_type": name, "artifact": artifact_name,
                "artifact_sha256": digest, "feature_names": list(FEATURE_NAMES),
                "threshold": 0.5, "approved": False,
                "validation_metrics": validation_metrics, "test_metrics": test_metrics,
                "train_sessions": train_dates, "validation_sessions": validation_dates,
                "test_sessions": test_dates,
            }
            manifest_path = self.root / f"{model_id}.json"
            manifest_tmp = manifest_path.with_suffix(".json.tmp")
            manifest_tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            os.replace(manifest_tmp, manifest_path)
            results.append(ModelTrainingResult(model_id, name, 0.5, validation_metrics, test_metrics, train_dates, validation_dates, test_dates, digest))
        return results

    def manifest(self, model_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"(?:lightgbm|xgboost)-[a-f0-9]{32}", model_id):
            raise ValueError("invalid model identifier")
        path = (self.root / f"{model_id}.json").resolve()
        if path.parent != self.root or not path.is_file():
            raise FileNotFoundError("model metadata was not found")
        payload = json.loads(path.read_text(encoding="utf-8"))
        artifact = (self.root / payload.get("artifact", "")).resolve()
        if artifact.parent != self.root or not artifact.is_file():
            raise FileNotFoundError("model artifact was not found")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if digest != payload.get("artifact_sha256"):
            raise ValueError("model artifact integrity check failed")
        return payload

    def predict_preview(self, model_id: str, bars: list[MinuteBar]) -> dict[str, Any]:
        try:
            import joblib
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("Install the optional ai dependencies to run inference") from exc
        metadata = self.manifest(model_id)
        artifact = self.root / metadata["artifact"]
        model = joblib.load(artifact)
        features = latest_features(bars)
        probability = float(model.predict_proba(np.asarray([features], dtype=np.float32))[0][1])
        threshold = float(metadata["threshold"])
        return {
            "model_id": model_id,
            "model_type": metadata["model_type"],
            "probability_positive": probability,
            "threshold": threshold,
            "signal": "BUY_CANDIDATE" if probability >= threshold else "HOLD",
            "approved": bool(metadata.get("approved", False)),
            "executable": False,
        }

