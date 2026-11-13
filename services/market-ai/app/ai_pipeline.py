"""Leakage-aware LightGBM/XGBoost training and local model registry."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .data_provenance import MIN_MODEL_TRAINING_SESSIONS, validate_data_provenance
from .market_calendar import exchange_continuous_session_window
from .quant import (MinuteBar, QuantConfig, _atr, _ema, _vwap,
                    _validate_session, backtest_orb_vwap, exchange_session_date, orb_vwap_entry_candidate)

FEATURE_NAMES = (
    "return_1m", "return_3m", "return_5m", "close_vwap_pct", "ema9_gap_pct",
    "ema20_gap_pct", "atr14_pct", "relative_volume20", "spread_bps", "session_progress",
)
FEATURE_VERSION = 3
LABEL_VERSION = 3
BACKTEST_VERSION = 6
BACKTEST_SPREAD_MODEL = "known_prior_bar_half_spread_entry_exit_v2"
MIN_MODEL_APPROVAL_TEST_SESSIONS = 5
MIN_MODEL_APPROVAL_TEST_TRADES = 5
MIN_MODEL_HOLDOUT_SELECTION_VALIDATION_SESSIONS = 3
MIN_MODEL_TRAIN_SAMPLES = 30
MIN_MODEL_VALIDATION_SAMPLES = 10
MIN_MODEL_TEST_SAMPLES = 10
_REGISTRY_LOCKS_GUARD = threading.Lock()
_REGISTRY_LOCKS: dict[Path, threading.RLock] = {}
_GOVERNANCE_GENESIS_SHA256 = "0" * 64


_MARKETS = {"KR", "US"}


def _session_progress(timestamp: datetime, market: str) -> Decimal:
    """Fraction of the local regular session elapsed, clamped to [0, 1]."""
    normalized_market = market.strip().upper()
    if normalized_market not in _MARKETS:
        raise ValueError("market must be KR or US")
    window = exchange_continuous_session_window(normalized_market, timestamp)
    if window is None:
        raise ValueError("timestamp must belong to an official market session")
    opened, closed = (bound.astimezone(timezone.utc) for bound in window)
    instant = timestamp.astimezone(timezone.utc)
    seconds = (instant - opened).total_seconds()
    duration_seconds = (closed - opened).total_seconds()
    return min(Decimal(1), max(Decimal(0), Decimal(seconds) / Decimal(duration_seconds)))


def _positive_probabilities(model: Any, features: Any) -> Any:
    """Validate binary classifier output shape, class order, and probabilities."""
    import numpy as np

    matrix = np.asarray(features, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2 or matrix.shape[0] == 0 or not np.isfinite(matrix).all():
        raise ValueError("model features must be a non-empty finite matrix")
    classes = getattr(model, "classes_", None)
    if classes is None or not np.array_equal(np.asarray(classes), np.asarray([0, 1])):
        raise ValueError("model classes must be ordered as [0, 1]")
    probabilities = np.asarray(model.predict_proba(matrix))
    if probabilities.shape != (matrix.shape[0], 2):
        raise ValueError("model must return one binary-class probability row per sample")
    if (not np.isfinite(probabilities).all() or np.any(probabilities < 0)
            or np.any(probabilities > 1)
            or not np.allclose(probabilities.sum(axis=1), 1.0, rtol=1e-5, atol=1e-6)):
        raise ValueError("model returned invalid binary-class probabilities")
    return probabilities[:, 1]


def _positive_probability(model: Any, features: tuple[float, ...] | list[float]) -> float:
    """Validate a binary classifier's positive-class output before it can affect a signal."""
    return float(_positive_probabilities(model, features)[0])


@dataclass(frozen=True)
class TrainingSample:
    session: date
    timestamp: str
    features: tuple[float, ...]
    label: int
    quant_candidate: bool = False
    forward_net_return_bps: float | None = None


@dataclass(frozen=True)
class ModelTrainingResult:
    model_id: str
    market: str
    model_type: str
    threshold: float
    validation_metrics: dict[str, float]
    test_metrics: dict[str, float]
    quant_baseline_test_metrics: dict[str, float]
    quant_backtest_test_metrics: dict[str, Any]
    ai_quant_backtest_test_metrics: dict[str, Any]
    training_config: dict[str, Any]
    train_sessions: tuple[str, ...]
    validation_sessions: tuple[str, ...]
    test_sessions: tuple[str, ...]
    artifact_sha256: str


def build_training_samples(
    sessions: dict[date, list[MinuteBar]], *, market: str, horizon_bars: int = 5,
    round_trip_cost_bps: Decimal = Decimal("10"), warmup_bars: int = 20,
    quant_config: QuantConfig = QuantConfig(),
) -> list[TrainingSample]:
    """Create samples using features available at t and an executable forward label.

    A signal at t is entered at the next bar open, using the spread observed by
    the signal close because the next bar's aggregated spread is only known later.
    The exit is the close ``horizon_bars`` after t. Sessions are processed
    independently, so labels never cross a close. Caller owns data licensing and
    corporate-action/symbol normalization.
    """
    if horizon_bars <= 0 or warmup_bars <= 0 or round_trip_cost_bps < 0:
        raise ValueError("horizon, warmup, and transaction cost settings are invalid")
    normalized_market = market.strip().upper()
    if normalized_market not in _MARKETS:
        raise ValueError("market must be KR or US")
    minimum_label_cost = quant_config.minimum_round_trip_cost_bps
    if minimum_label_cost <= 0 or round_trip_cost_bps < minimum_label_cost:
        raise ValueError("label round-trip cost must cover modeled buy/sell fees, tax, and slippage")
    samples: list[TrainingSample] = []
    required = max(warmup_bars, 21, 15, 10)
    for session, bars in sorted(sessions.items()):
        if not bars:
            continue
        if any(exchange_session_date(item.timestamp, normalized_market) != session for item in bars):
            raise ValueError("bar date does not match its session key")
        _validate_session(bars, normalized_market, require_complete=True)
        for index in range(required - 1, len(bars) - horizon_bars):
            current = bars[index]
            history = bars[: index + 1]
            prior = bars[max(0, index - 20):index]
            mean_volume = Decimal(sum(item.volume for item in prior)) / len(prior) if prior else Decimal(0)
            volume_ratio = Decimal(current.volume) / mean_volume if mean_volume else Decimal(0)
            if current.spread_bps is None or current.spread_observed_at is None:
                continue
            entry = bars[index + 1]
            close = current.close
            returns = [
                Decimal(0) if index < period else close / bars[index - period].close - Decimal(1)
                for period in (1, 3, 5)
            ]
            vwap = _vwap(history)
            ema9 = _ema([item.close for item in history], 9)
            ema20 = _ema([item.close for item in history], 20)
            atr_pct = _atr(history, 14) / close
            session_progress = _session_progress(current.timestamp, normalized_market)
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
            future = bars[index + horizon_bars]
            if future.spread_bps is None or future.spread_observed_at is None:
                continue
            future_return = future.close / entry.open - Decimal(1)
            observed_round_trip_spread = (current.spread_bps + future.spread_bps) / Decimal(2)
            forward_net_return_bps = (
                future_return * Decimal(10_000) - round_trip_cost_bps - observed_round_trip_spread
            )
            label = int(forward_net_return_bps > 0)
            quant_candidate = orb_vwap_entry_candidate(history, quant_config, market=normalized_market)
            samples.append(TrainingSample(session, current.timestamp.isoformat(),
                                          tuple(float(value) for value in feature_values), label, quant_candidate,
                                          float(forward_net_return_bps)))
    return samples


def latest_features(bars: list[MinuteBar], *, market: str, minimum_bars: int = 21) -> tuple[float, ...]:
    """Build the current-bar feature vector without reading a future observation."""
    required_bars = max(21, minimum_bars)
    if len(bars) < required_bars:
        raise ValueError(f"at least {required_bars} bars are required for inference")
    normalized_market = market.strip().upper()
    if normalized_market not in _MARKETS:
        raise ValueError("market must be KR or US")
    _validate_session(bars, normalized_market)
    current = bars[-1]
    if current.spread_bps is None or current.spread_observed_at is None:
        raise ValueError("latest bar must include a timestamped spread")
    index = len(bars) - 1
    prior = bars[max(0, index - 20):index]
    mean_volume = Decimal(sum(item.volume for item in prior)) / len(prior) if prior else Decimal(0)
    volume_ratio = Decimal(current.volume) / mean_volume if mean_volume else Decimal(0)
    close = current.close
    features = (
        *(Decimal(0) if index < period else close / bars[index - period].close - Decimal(1) for period in (1, 3, 5)),
        close / _vwap(bars) - Decimal(1),
        close / _ema([item.close for item in bars], 9) - Decimal(1),
        close / _ema([item.close for item in bars], 20) - Decimal(1),
        _atr(bars, 14) / close,
        volume_ratio,
        current.spread_bps,
        _session_progress(current.timestamp, normalized_market),
    )
    return tuple(float(value) for value in features)


def predict_probability_series(model: Any, bars: list[MinuteBar], *, market: str,
                               minimum_bars: int = 21) -> dict[str, float]:
    """Score each bar using only data through that bar close for offline engine parity."""
    if not isinstance(minimum_bars, int) or isinstance(minimum_bars, bool) or minimum_bars < 21:
        raise ValueError("minimum_bars must be an integer of at least 21")
    if not bars:
        raise ValueError("bars are required for model scoring")
    _validate_session(bars, market.strip().upper())
    scores: dict[str, float] = {}
    for index in range(minimum_bars - 1, len(bars)):
        history = bars[:index + 1]
        features = latest_features(history, market=market, minimum_bars=minimum_bars)
        probability = _positive_probability(model, features)
        scores[bars[index].timestamp.isoformat()] = probability
    return scores


def _chronological_partitions(samples: list[TrainingSample]) -> tuple[list[TrainingSample], list[TrainingSample], list[TrainingSample]]:
    dates = sorted({sample.session for sample in samples})
    if len(dates) < 3:
        raise ValueError("at least three distinct sessions are required for train/validation/test splits")
    if len(dates) >= MIN_MODEL_TRAINING_SESSIONS:
        # Production datasets must retain enough chronological validation and test
        # sessions for candidate selection and the independent approval gate.
        test_count = max(MIN_MODEL_APPROVAL_TEST_SESSIONS, math.ceil(len(dates) * 0.2))
        validation_count = max(MIN_MODEL_HOLDOUT_SELECTION_VALIDATION_SESSIONS,
                               math.ceil(len(dates) * 0.2))
        test_start = len(dates) - test_count
        validation_start = test_start - validation_count
        if validation_start < 1:
            raise ValueError(
                f"at least {MIN_MODEL_TRAINING_SESSIONS} distinct sessions are required for approval-ready partitions"
            )
        train_dates = set(dates[:validation_start])
        validation_dates = set(dates[validation_start:test_start])
        test_dates = set(dates[test_start:])
    else:
        # Small synthetic/unit datasets can exercise candidate generation, but can
        # never meet the production model approval session requirements.
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


def assess_training_samples(samples: list[TrainingSample]) -> dict[str, Any]:
    """Report the same approval-ready data partition gates required by model training."""
    dates = sorted({sample.session for sample in samples})
    positive = sum(sample.label == 1 for sample in samples)
    negative = sum(sample.label == 0 for sample in samples)
    result: dict[str, Any] = {
        "ready": False,
        "sample_count": len(samples),
        "session_count": len(dates),
        "positive_label_count": positive,
        "negative_label_count": negative,
        "partition_sample_counts": None,
        "partition_session_counts": None,
        "reasons": [],
    }
    if len(dates) < MIN_MODEL_TRAINING_SESSIONS:
        result["reasons"] = ["insufficient_eligible_sessions"]
        return result
    try:
        train, validation, test = _chronological_partitions(samples)
    except ValueError:
        result["reasons"] = ["chronological_partitioning_failed"]
        return result
    partitions = {"train": train, "validation": validation, "test": test}
    result["partition_sample_counts"] = {name: len(rows) for name, rows in partitions.items()}
    result["partition_session_counts"] = {
        name: len({sample.session for sample in rows}) for name, rows in partitions.items()
    }
    reasons = []
    if (len(train) < MIN_MODEL_TRAIN_SAMPLES
            or len(validation) < MIN_MODEL_VALIDATION_SAMPLES
            or len(test) < MIN_MODEL_TEST_SAMPLES):
        reasons.append("insufficient_samples_per_partition")
    if len({sample.label for sample in train}) < 2:
        reasons.append("training_partition_requires_both_outcome_classes")
    result["reasons"] = reasons
    result["ready"] = not reasons
    return result


def _metrics(model: Any, samples: list[TrainingSample]) -> dict[str, float]:
    from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score

    x = [sample.features for sample in samples]
    y = [sample.label for sample in samples]
    probabilities = _positive_probabilities(model, x)
    predictions = (probabilities >= 0.5).astype(int)
    metrics = {"accuracy": float(accuracy_score(y, predictions)), "brier": float(brier_score_loss(y, probabilities))}
    metrics["roc_auc"] = float(roc_auc_score(y, probabilities)) if len(set(y)) == 2 else 0.5
    # P5 runs inference only after ORB/VWAP passes, so evaluate the same hybrid candidate here.
    predicted_candidates = [
        sample.label for sample, prediction in zip(samples, predictions)
        if prediction == 1 and sample.quant_candidate
    ]
    selected_net_returns = [
        sample.forward_net_return_bps
        for sample, prediction in zip(samples, predictions)
        if prediction == 1 and sample.quant_candidate and sample.forward_net_return_bps is not None
    ]
    metrics["candidate_count"] = float(len(predicted_candidates))
    metrics["candidate_coverage"] = float(len(predicted_candidates) / len(samples))
    metrics["candidate_positive_rate"] = (
        float(sum(predicted_candidates) / len(predicted_candidates)) if predicted_candidates else 0.0
    )
    metrics["candidate_mean_forward_net_bps"] = (
        float(sum(selected_net_returns) / len(selected_net_returns)) if selected_net_returns else 0.0
    )
    return metrics


def _quant_baseline_metrics(samples: list[TrainingSample]) -> dict[str, float]:
    selected = [sample for sample in samples if sample.quant_candidate]
    candidates = [sample.label for sample in selected]
    net_returns = [sample.forward_net_return_bps for sample in selected if sample.forward_net_return_bps is not None]
    return {
        "candidate_count": float(len(candidates)),
        "candidate_coverage": float(len(candidates) / len(samples)) if samples else 0.0,
        "candidate_positive_rate": float(sum(candidates) / len(candidates)) if candidates else 0.0,
        "candidate_mean_forward_net_bps": float(sum(net_returns) / len(net_returns)) if net_returns else 0.0,
    }


def _backtest_metrics(results: list[Any]) -> dict[str, Any]:
    trades = [trade for result in results for trade in result.trades]
    net_pnl = sum((result.net_pnl for result in results), Decimal(0))
    return {
        "session_count": float(len(results)),
        "signal_count": float(sum(result.signals for result in results)),
        "trade_count": float(len(trades)),
        "winning_trades": float(sum(trade.net_pnl > 0 for trade in trades)),
        "win_rate": float(sum(trade.net_pnl > 0 for trade in trades) / len(trades)) if trades else 0.0,
        "gross_pnl": str(sum((result.gross_pnl for result in results), Decimal(0))),
        "estimated_costs": str(sum((result.costs for result in results), Decimal(0))),
        "net_pnl": str(net_pnl),
        "average_trade_net_pnl": str(net_pnl / len(trades)) if trades else "0",
    }


class LocalModelRegistry:
    """Stores model artifacts outside source control; newly trained models stay unapproved."""

    def __init__(self, root: str | Path = ".local/models"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with _REGISTRY_LOCKS_GUARD:
            self._governance_lock = _REGISTRY_LOCKS.setdefault(self.root, threading.RLock())

    @property
    def _active_path(self) -> Path:
        return self.root / "active-model.json"

    @property
    def _active_models_path(self) -> Path:
        return self.root / "active-models.json"

    @property
    def _audit_path(self) -> Path:
        return self.root / "governance-audit.jsonl"

    def list_models(self) -> list[dict[str, Any]]:
        try:
            active_model_ids = {item["model_id"] for item in self._read_active_models().values()}
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            active_model_ids = set()
        items: list[dict[str, Any]] = []
        for manifest_path in sorted(self.root.glob("*.json"), key=lambda path: path.name):
            if manifest_path in {self._active_path, self._active_models_path}:
                continue
            try:
                metadata = self.manifest(manifest_path.stem)
                data_rights_verified = self._has_verified_data_provenance(metadata)
                feature_compatible = self._supports_current_features(metadata)
                label_compatible = self._supports_current_label(metadata)
                model_compatible = feature_compatible and label_compatible
                approval_evidence_verified = (
                    self._has_valid_approval_evidence(metadata)
                    and self._has_durable_holdout_evaluation(metadata)
                )
                approval_audit_verified = (self._has_durable_approval_event(metadata)
                                            if metadata.get("approved") else None)
                approved = bool(metadata.get("approved", False) and data_rights_verified
                                and model_compatible and approval_evidence_verified
                                and approval_audit_verified)
                items.append({
                    "model_id": metadata["model_id"], "market": metadata.get("market"), "model_type": metadata["model_type"],
                    "feature_version": metadata.get("feature_version"),
                    "feature_compatible": feature_compatible,
                    "label_version": metadata.get("training_config", {}).get("label_version"),
                    "label_compatible": label_compatible,
                    "model_compatible": model_compatible,
                    "data_rights_verified": data_rights_verified,
                    "approval_evidence_verified": approval_evidence_verified,
                    "approval_audit_verified": approval_audit_verified,
                    "approved": approved,
                    "active": metadata["model_id"] in active_model_ids and approved and model_compatible,
                    "artifact_sha256": metadata["artifact_sha256"],
                    "dataset_sha256": metadata.get("dataset_sha256"),
                    "validation_metrics": metadata.get("validation_metrics", {}),
                    "test_metrics": metadata.get("test_metrics", {}),
                    "quant_baseline_test_metrics": metadata.get("quant_baseline_test_metrics", {}),
                    "quant_backtest_test_metrics": metadata.get("quant_backtest_test_metrics", {}),
                    "ai_quant_backtest_test_metrics": metadata.get("ai_quant_backtest_test_metrics", {}),
                    "training_config": metadata.get("training_config", {}),
                    "test_sessions": metadata.get("test_sessions", []),
                    "approval": metadata.get("approval"),
                })
            except (FileNotFoundError, ValueError, KeyError, OSError, json.JSONDecodeError):
                items.append({"model_id": manifest_path.stem, "integrity_error": True, "approved": False, "active": False})
        return items

    def select_candidate_for_holdout(self, market: str, symbol: str,
                                     holdout_sessions: dict[date, list[MinuteBar]]) -> dict[str, Any]:
        """Select exactly one candidate by validation Brier before consuming later holdout data."""
        normalized_market = market.strip().upper()
        normalized_symbol = symbol.strip().upper()
        if normalized_market not in _MARKETS or not normalized_symbol or not holdout_sessions:
            raise ValueError("market, symbol, and independent holdout sessions are required")
        holdout_start = min(holdout_sessions)
        eligible: list[tuple[float, str, int, str, str]] = []
        with self._governance_lock:
            for item in self.list_models():
                if (item.get("integrity_error") or item.get("approved")
                        or item.get("market") != normalized_market
                        or item.get("data_rights_verified") is not True
                        or item.get("model_compatible") is not True):
                    continue
                model_id = item.get("model_id")
                if not isinstance(model_id, str):
                    continue
                try:
                    metadata = self.manifest(model_id)
                    if (metadata.get("approved") is True or metadata.get("heldout_evaluation") is not None
                            or metadata.get("training_config", {}).get("symbol") != normalized_symbol):
                        continue
                    partitions = [metadata.get(name) for name in
                                  ("train_sessions", "validation_sessions", "test_sessions")]
                    if any(not isinstance(values, list) or not values
                           or any(not isinstance(value, str) for value in values) for values in partitions):
                        continue
                    parsed = [[date.fromisoformat(value) for value in values] for values in partitions]
                    if any(len(values) != len(set(values)) for values in parsed):
                        continue
                    train_sessions, validation_sessions, prior_test_sessions = map(set, parsed)
                    if (train_sessions & validation_sessions or train_sessions & prior_test_sessions
                            or validation_sessions & prior_test_sessions
                            or max(train_sessions) >= min(validation_sessions)
                            or max(validation_sessions) >= min(prior_test_sessions)
                            or max(prior_test_sessions) >= holdout_start):
                        continue
                    if len(validation_sessions) < MIN_MODEL_HOLDOUT_SELECTION_VALIDATION_SESSIONS:
                        continue
                    metrics = metadata.get("validation_metrics")
                    brier = metrics.get("brier") if isinstance(metrics, dict) else None
                    if (isinstance(brier, bool) or not isinstance(brier, (int, float))
                            or not math.isfinite(float(brier)) or not 0 <= float(brier) <= 1):
                        continue
                    cohort = json.dumps({
                        "dataset_sha256": metadata.get("dataset_sha256"),
                        "training_config": metadata.get("training_config"),
                        "train_sessions": sorted(value.isoformat() for value in train_sessions),
                        "validation_sessions": sorted(value.isoformat() for value in validation_sessions),
                        "test_sessions": sorted(value.isoformat() for value in prior_test_sessions),
                    }, sort_keys=True, separators=(",", ":"))
                    cohort_sha256 = hashlib.sha256(cohort.encode("utf-8")).hexdigest()
                    latest_training_session = max(prior_test_sessions).isoformat()
                    eligible.append((float(brier), model_id, len(validation_sessions),
                                     latest_training_session, cohort_sha256))
                except (FileNotFoundError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    continue
        if not eligible:
            raise ValueError(
                "no unapproved candidate has three distinct prior validation sessions and a valid Brier score"
            )
        latest_training_session = max(value[3] for value in eligible)
        latest_candidates = [value for value in eligible if value[3] == latest_training_session]
        latest_cohorts = {value[4] for value in latest_candidates}
        if len(latest_cohorts) != 1:
            raise ValueError(
                "multiple candidate training cohorts share the latest test session; refusing incomparable selection"
            )
        comparable_candidates = [value for value in latest_candidates if value[4] in latest_cohorts]
        brier, model_id, validation_session_count, _, _ = min(
            comparable_candidates, key=lambda value: (value[0], value[1])
        )
        return {
            "model_id": model_id,
            "selection_method": "lowest_validation_brier",
            "validation_brier": brier,
            "validation_session_count": validation_session_count,
            "eligible_candidate_count": len(comparable_candidates),
        }

    def active_approved_model(self, market: str, symbol: str) -> dict[str, Any] | None:
        """Return the integrity-checked model only when approved for this market and trained symbol."""
        requested_market = market.strip().upper()
        requested_symbol = symbol.strip().upper()
        if requested_market not in {"KR", "US"} or not requested_symbol:
            return None
        try:
            active_payload = self._read_active_models()
            entry = active_payload.get(self._model_scope(requested_market, requested_symbol))
            if not isinstance(entry, dict):
                return None
            active_id = entry.get("model_id")
            if not isinstance(active_id, str):
                return None
            metadata = self.manifest(active_id)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None
        if (metadata.get("market") != requested_market
                or str(metadata.get("training_config", {}).get("symbol", "")).strip().upper() != requested_symbol
                or not metadata.get("approved")
                or not self._supports_current_model_contract(metadata)
                or not self._has_verified_data_provenance(metadata)
                or not self._has_valid_approval_evidence(metadata)
                or not self._has_durable_holdout_evaluation(metadata)
                or not self._has_durable_approval_event(metadata)):
            return None
        return metadata

    def active_approved_entry_filter(
        self, market: str, symbol: str,
    ) -> tuple[str, Callable[[list[MinuteBar]], bool]] | None:
        """Load a scoped, approved active classifier once for quant-entry filtering."""
        metadata = self.active_approved_model(market, symbol)
        if metadata is None:
            return None
        try:
            import joblib
        except ImportError as exc:
            raise RuntimeError("Install the optional ai dependencies to run inference") from exc
        # Re-read the manifest so the artifact digest is verified immediately before load.
        verified = self.manifest(metadata["model_id"])
        if (verified.get("artifact_sha256") != metadata.get("artifact_sha256")
                or verified.get("market") != market.strip().upper()
                or str(verified.get("training_config", {}).get("symbol", "")).strip().upper()
                != symbol.strip().upper()):
            raise ValueError("active model scope or artifact changed during inference setup")
        model = joblib.load(self.root / verified["artifact"])
        threshold = float(verified["threshold"])
        normalized_market = market.strip().upper()

        def entry_filter(bars: list[MinuteBar]) -> bool:
            features = latest_features(bars, market=normalized_market)
            return bool(_positive_probability(model, features) >= threshold)

        return metadata["model_id"], entry_filter

    def validate_model_backtest_sessions(self, model_id: str, sessions: set[date]) -> dict[str, Any]:
        """Return a model manifest only when every inference session is strictly out of sample."""
        if not sessions:
            raise ValueError("out-of-sample portfolio sessions are required")
        metadata = self.manifest(model_id)
        model_dates: set[date] = set()
        for field in ("train_sessions", "validation_sessions", "test_sessions"):
            values = metadata.get(field)
            if (not isinstance(values, list) or not values
                    or any(not isinstance(value, str) for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError(f"model {field} are invalid")
            try:
                model_dates.update(date.fromisoformat(value) for value in values)
            except ValueError as exc:
                raise ValueError(f"model {field} contain an invalid date") from exc
        evaluation = metadata.get("heldout_evaluation")
        holdout_values = evaluation.get("sessions") if isinstance(evaluation, dict) else None
        if (not isinstance(holdout_values, list) or not holdout_values
                or any(not isinstance(value, str) for value in holdout_values)
                or len(set(holdout_values)) != len(holdout_values)):
            raise ValueError("model holdout sessions are invalid")
        try:
            model_dates.update(date.fromisoformat(value) for value in holdout_values)
        except ValueError as exc:
            raise ValueError("model holdout sessions contain an invalid date") from exc
        if max(model_dates) >= min(sessions):
            raise ValueError("portfolio backtest sessions must be strictly later than all model training and holdout data")
        return metadata

    def _read_active_models(self) -> dict[str, dict[str, Any]]:
        """Read the scoped activation registry, migrating the legacy single pointer in memory."""
        if self._active_models_path.is_file():
            payload = json.loads(self._active_models_path.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict) or payload.get("schema_version") != 1
                    or not isinstance(payload.get("models"), dict)):
                raise ValueError("active model registry has an unsupported or corrupt format")
            active = payload["models"]
            for scope, entry in active.items():
                if (not isinstance(scope, str) or not isinstance(entry, dict)
                        or not isinstance(entry.get("model_id"), str)
                        or not re.fullmatch(r"(?:lightgbm|xgboost)-[a-f0-9]{32}", entry["model_id"])):
                    raise ValueError("active model registry contains an invalid scope entry")
                market, separator, symbol = scope.partition(":")
                valid_symbol = (bool(re.fullmatch(r"\d{6}", symbol)) if market == "KR"
                                else bool(re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol)) if market == "US"
                                else False)
                if not separator or not valid_symbol or self._model_scope(market, symbol) != scope:
                    raise ValueError("active model registry contains an invalid market-symbol scope")
                metadata = self.manifest(entry["model_id"])
                trained_market = str(metadata.get("market", "")).strip().upper()
                trained_symbol = str(metadata.get("training_config", {}).get("symbol", "")).strip().upper()
                if self._model_scope(trained_market, trained_symbol) != scope:
                    raise ValueError("active model registry scope does not match the model manifest")
            return active
        if not self._active_path.is_file():
            return {}
        legacy = json.loads(self._active_path.read_text(encoding="utf-8"))
        if not isinstance(legacy, dict) or not isinstance(legacy.get("model_id"), str):
            raise ValueError("legacy active model pointer is invalid")
        metadata = self.manifest(legacy["model_id"])
        market = str(metadata.get("market", "")).strip().upper()
        symbol = str(metadata.get("training_config", {}).get("symbol", "")).strip().upper()
        if market not in {"KR", "US"} or not symbol:
            raise ValueError("legacy active model lacks a valid market and symbol scope")
        return {self._model_scope(market, symbol): legacy}

    @staticmethod
    def _model_scope(market: str, symbol: str) -> str:
        return f"{market.strip().upper()}:{symbol.strip().upper()}"

    def _has_durable_approval_event(self, metadata: dict[str, Any]) -> bool:
        """Require the approval state to match a complete, durable governance event."""
        approval = metadata.get("approval")
        if not isinstance(approval, dict) or not self._audit_path.is_file():
            return False
        try:
            events = self._read_governance_events()
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        expected = {
            "action": "approve",
            "model_id": metadata.get("model_id"),
            "approved_by": approval.get("approved_by"),
            "note": approval.get("note"),
            "approved_at": approval.get("approved_at"),
            "dataset_sha256": metadata.get("dataset_sha256"),
            "artifact_sha256": metadata.get("artifact_sha256"),
            "threshold": metadata.get("threshold"),
            "heldout_evaluation": metadata.get("heldout_evaluation"),
            "train_sessions": metadata.get("train_sessions"),
            "validation_sessions": metadata.get("validation_sessions"),
            "test_sessions": metadata.get("test_sessions"),
            "training_config": metadata.get("training_config", {}),
            "quant_backtest_test_metrics": metadata.get("quant_backtest_test_metrics"),
            "ai_quant_backtest_test_metrics": metadata.get("ai_quant_backtest_test_metrics"),
        }
        return any(all(event.get(key) == value for key, value in expected.items()) for event in events)

    def _has_durable_holdout_evaluation(self, metadata: dict[str, Any]) -> bool:
        """Require independent holdout metrics to match their immutable audit event."""
        evaluation = metadata.get("heldout_evaluation")
        if not isinstance(evaluation, dict) or not self._audit_path.is_file():
            return False
        try:
            events = self._read_governance_events()
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        expected = {
            "action": "evaluate_holdout",
            "model_id": metadata.get("model_id"),
            "market": evaluation.get("market"),
            "symbol": evaluation.get("symbol"),
            "candidate_selection": evaluation.get("candidate_selection"),
            "dataset_sha256": evaluation.get("dataset_sha256"),
            "data_provenance": evaluation.get("data_provenance"),
            "artifact_sha256": metadata.get("artifact_sha256"),
            "threshold": metadata.get("threshold"),
            "test_sessions": evaluation.get("sessions"),
            "evaluated_at": evaluation.get("evaluated_at"),
            "quant_backtest_test_metrics": metadata.get("quant_backtest_test_metrics"),
            "ai_quant_backtest_test_metrics": metadata.get("ai_quant_backtest_test_metrics"),
        }
        return any(all(event.get(key) == value for key, value in expected.items()) for event in events)

    @staticmethod
    def _governance_event_sha256(event: dict[str, Any]) -> str:
        payload = {key: value for key, value in event.items() if key != "event_sha256"}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _read_governance_events(self) -> list[dict[str, Any]]:
        if not self._audit_path.is_file():
            return []
        previous_sha256 = _GOVERNANCE_GENESIS_SHA256
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(self._audit_path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"governance audit event {line_number} is invalid JSON") from exc
            if not isinstance(event, dict):
                raise ValueError(f"governance audit event {line_number} is not an object")
            if (event.get("previous_event_sha256") != previous_sha256
                    or event.get("event_sha256") != self._governance_event_sha256(event)):
                raise ValueError(f"governance audit chain failed at event {line_number}")
            previous_sha256 = str(event["event_sha256"])
            events.append(event)
        return events

    def governance_audit_status(self) -> dict[str, Any]:
        """Expose audit-chain integrity separately from the event list."""
        with self._governance_lock:
            try:
                events = self._read_governance_events()
                return {"integrity_verified": True, "event_count": len(events)}
            except (OSError, ValueError, json.JSONDecodeError):
                return {"integrity_verified": False, "event_count": 0}

    @staticmethod
    def _supports_current_features(metadata: dict[str, Any]) -> bool:
        return (metadata.get("feature_version") == FEATURE_VERSION
                and metadata.get("feature_names") == list(FEATURE_NAMES))

    @staticmethod
    def _supports_current_label(metadata: dict[str, Any]) -> bool:
        training_config = metadata.get("training_config")
        return isinstance(training_config, dict) and training_config.get("label_version") == LABEL_VERSION

    @staticmethod
    def _supports_current_model_contract(metadata: dict[str, Any]) -> bool:
        return (LocalModelRegistry._supports_current_features(metadata)
                and LocalModelRegistry._supports_current_label(metadata))

    @staticmethod
    def _has_verified_data_provenance(metadata: dict[str, Any]) -> bool:
        try:
            validate_data_provenance(metadata.get("data_provenance"),
                                     expected_sha256=metadata.get("dataset_sha256"),
                                     purpose="model_training")
            return True
        except ValueError:
            return False

    @staticmethod
    def _has_valid_approval_evidence(metadata: dict[str, Any]) -> bool:
        try:
            evaluation = metadata.get("heldout_evaluation")
            if not isinstance(evaluation, dict):
                return False
            if (evaluation.get("market") != metadata.get("market")
                    or evaluation.get("symbol") != metadata.get("training_config", {}).get("symbol")):
                return False
            selection = evaluation.get("candidate_selection")
            if (not isinstance(selection, dict)
                    or selection.get("model_id") != metadata.get("model_id")
                    or selection.get("selection_method") != "lowest_validation_brier"
                    or isinstance(selection.get("validation_brier"), bool)
                    or not isinstance(selection.get("validation_brier"), (int, float))
                    or not math.isfinite(float(selection["validation_brier"]))
                    or not 0 <= float(selection["validation_brier"]) <= 1
                    or selection.get("validation_session_count", 0)
                    < MIN_MODEL_HOLDOUT_SELECTION_VALIDATION_SESSIONS
                    or selection.get("eligible_candidate_count", 0) < 1):
                return False
            if evaluation.get("model_artifact_sha256") != metadata.get("artifact_sha256"):
                return False
            threshold = metadata.get("threshold")
            evaluated_threshold = evaluation.get("threshold")
            if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                    or not math.isfinite(float(threshold)) or not 0 <= threshold <= 1
                    or isinstance(evaluated_threshold, bool)
                    or not isinstance(evaluated_threshold, (int, float))
                    or float(evaluated_threshold) != float(threshold)):
                return False
            validate_data_provenance(
                evaluation.get("data_provenance"),
                expected_sha256=evaluation.get("dataset_sha256"),
                purpose="backtesting",
            )
            partitions: dict[str, list[date]] = {}
            for name in ("train_sessions", "validation_sessions", "test_sessions"):
                values = metadata.get(name)
                if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
                    return False
                parsed = [date.fromisoformat(value) for value in values]
                if len(set(parsed)) != len(parsed):
                    return False
                partitions[name] = parsed
            train, validation, test = (set(partitions[name]) for name in
                                       ("train_sessions", "validation_sessions", "test_sessions"))
            if (train & validation or train & test or validation & test
                    or max(train) >= min(validation) or max(validation) >= min(test)
                    or len(test) < MIN_MODEL_APPROVAL_TEST_SESSIONS):
                return False
            if set(evaluation.get("sessions", [])) != {value.isoformat() for value in test}:
                return False
            ai = metadata.get("ai_quant_backtest_test_metrics")
            quant = metadata.get("quant_backtest_test_metrics")
            if not isinstance(ai, dict) or not isinstance(quant, dict):
                return False
            config = metadata.get("training_config")
            if (not isinstance(config, dict) or not isinstance(config.get("symbol"), str)
                    or not config["symbol"].strip() or config.get("backtest_quantity_per_session") != 1):
                return False
            horizon = config.get("label_horizon_bars")
            if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
                return False
            if (config.get("backtest_version") != BACKTEST_VERSION
                    or config.get("backtest_spread_model") != BACKTEST_SPREAD_MODEL):
                return False
            label_cost = Decimal(str(config["label_round_trip_cost_bps"]))
            buy_commission = Decimal(str(config["backtest_buy_commission_bps"]))
            sell_commission = Decimal(str(config["backtest_sell_commission_bps"]))
            sell_tax = Decimal(str(config["backtest_sell_tax_bps"]))
            slippage = Decimal(str(config["backtest_slippage_bps"]))
            if any(not value.is_finite() or value < 0 for value in
                   (label_cost, buy_commission, sell_commission, sell_tax, slippage)):
                return False
            # Labels span one buy and one sell. The floor includes direction-specific
            # commissions, sell-side tax, and slippage on both fills.
            if (buy_commission + sell_commission + sell_tax + 2 * slippage <= 0
                    or label_cost < buy_commission + sell_commission + sell_tax + 2 * slippage):
                return False
            for metrics in (ai, quant):
                session_count = Decimal(str(metrics["session_count"]))
                if not session_count.is_finite() or session_count != len(test):
                    return False
            ai_net = Decimal(str(ai["net_pnl"]))
            quant_net = Decimal(str(quant["net_pnl"]))
            ai_trades = Decimal(str(ai["trade_count"]))
            return (ai_trades.is_finite() and ai_trades == ai_trades.to_integral_value()
                    and ai_trades >= MIN_MODEL_APPROVAL_TEST_TRADES
                    and ai_net.is_finite() and quant_net.is_finite()
                    and ai_net > 0 and ai_net > quant_net)
        except (ArithmeticError, KeyError, TypeError, ValueError):
            return False

    def approve(self, model_id: str, *, approved_by: str, note: str) -> dict[str, Any]:
        reviewer = approved_by.strip()[:80]
        justification = note.strip()[:1000]
        if not reviewer or len(justification) < 20:
            raise ValueError("reviewer and an approval note of at least 20 characters are required")
        with self._governance_lock:
            metadata = self.manifest(model_id)
            if metadata.get("market") not in {"KR", "US"}:
                raise ValueError("market scope must be KR or US before model approval")
            if not self._supports_current_model_contract(metadata):
                raise ValueError("model feature or label schema is outdated and must be retrained")
            if not self._has_verified_data_provenance(metadata):
                raise ValueError("model lacks verified data-rights provenance and cannot be approved")
            if not metadata.get("test_sessions") or not metadata.get("test_metrics"):
                raise ValueError("model has no held-out test evidence and cannot be approved")
            threshold = metadata.get("threshold")
            if (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                    or not math.isfinite(float(threshold)) or not 0 <= threshold <= 1):
                raise ValueError("model decision threshold must be finite and within [0, 1]")
            session_partitions: dict[str, set[date]] = {}
            for partition_name in ("train_sessions", "validation_sessions", "test_sessions"):
                values = metadata.get(partition_name)
                if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
                    raise ValueError("model session partitions are incomplete and cannot be approved")
                try:
                    parsed_values = [date.fromisoformat(value) for value in values]
                except ValueError as exc:
                    raise ValueError("model session partitions must contain ISO dates") from exc
                if len(set(parsed_values)) != len(parsed_values):
                    raise ValueError("model session partitions must not contain duplicate dates")
                session_partitions[partition_name] = set(parsed_values)
            train_sessions = session_partitions["train_sessions"]
            validation_sessions = session_partitions["validation_sessions"]
            test_sessions = session_partitions["test_sessions"]
            if (train_sessions & validation_sessions or train_sessions & test_sessions
                    or validation_sessions & test_sessions
                    or max(train_sessions) >= min(validation_sessions)
                    or max(validation_sessions) >= min(test_sessions)):
                raise ValueError("model session partitions must be disjoint and strictly chronological")
            if len(test_sessions) < MIN_MODEL_APPROVAL_TEST_SESSIONS:
                raise ValueError("at least five distinct held-out sessions are required for model approval")
            ai_backtest = metadata.get("ai_quant_backtest_test_metrics")
            quant_backtest = metadata.get("quant_backtest_test_metrics")
            if not isinstance(ai_backtest, dict) or not isinstance(quant_backtest, dict):
                raise ValueError("held-out AI+quant and quant-only strategy backtests are required for approval")
            try:
                ai_trade_count = int(ai_backtest["trade_count"])
                ai_net_pnl = Decimal(str(ai_backtest["net_pnl"]))
                quant_net_pnl = Decimal(str(quant_backtest["net_pnl"]))
            except (KeyError, ArithmeticError, TypeError, ValueError) as exc:
                raise ValueError("held-out strategy backtest metrics are incomplete") from exc
            if ai_trade_count < MIN_MODEL_APPROVAL_TEST_TRADES:
                raise ValueError("at least five held-out AI+quant strategy trades are required for model approval")
            if not ai_net_pnl.is_finite() or not quant_net_pnl.is_finite():
                raise ValueError("held-out strategy backtest metrics must be finite")
            if ai_net_pnl <= 0 or ai_net_pnl <= quant_net_pnl:
                raise ValueError("AI+quant held-out net PnL must be positive and exceed the quant-only baseline")
            if not self._has_valid_approval_evidence(metadata):
                raise ValueError("held-out metrics or training configuration are inconsistent")
            if not self._has_durable_holdout_evaluation(metadata):
                raise ValueError("held-out evidence lacks a matching durable evaluation audit event")
            previous_metadata = json.loads(json.dumps(metadata))
            metadata["approved"] = True
            metadata["approval"] = {"approved_by": reviewer, "note": justification, "approved_at": datetime.now().astimezone().isoformat()}
            self._write_json_atomic(self.root / f"{model_id}.json", metadata)
            try:
                self._record_governance_event({
                    "action": "approve", "model_id": model_id, **metadata["approval"],
                    "dataset_sha256": metadata.get("dataset_sha256"),
                    "artifact_sha256": metadata["artifact_sha256"],
                    "threshold": metadata["threshold"],
                    "heldout_evaluation": metadata["heldout_evaluation"],
                    "train_sessions": metadata["train_sessions"],
                    "validation_sessions": metadata["validation_sessions"],
                    "test_sessions": metadata["test_sessions"],
                    "training_config": metadata.get("training_config", {}),
                    "quant_backtest_test_metrics": quant_backtest,
                    "ai_quant_backtest_test_metrics": ai_backtest,
                })
            except (OSError, ValueError):
                self._write_json_atomic(self.root / f"{model_id}.json", previous_metadata)
                raise
            return metadata

    def activate(self, model_id: str, *, changed_by: str, note: str, action: str = "activate") -> dict[str, Any]:
        operator = changed_by.strip()[:80]
        justification = note.strip()[:1000]
        if not operator or len(justification) < 20:
            raise ValueError("operator and a change note of at least 20 characters are required")
        with self._governance_lock:
            metadata = self.manifest(model_id)
            if not self._supports_current_model_contract(metadata):
                raise ValueError("model feature schema is outdated and cannot be activated")
            if not self._has_verified_data_provenance(metadata):
                raise ValueError("model lacks verified data-rights provenance and cannot be activated")
            if not metadata.get("approved"):
                raise ValueError("only an approved model can become the advisory active model")
            if not self._has_valid_approval_evidence(metadata):
                raise ValueError("model lacks valid held-out comparative strategy evidence")
            if not self._has_durable_approval_event(metadata):
                raise ValueError("model approval lacks a durable matching governance audit event")
            market = str(metadata.get("market", "")).strip().upper()
            symbol = str(metadata.get("training_config", {}).get("symbol", "")).strip().upper()
            if market not in {"KR", "US"} or not symbol:
                raise ValueError("model market and symbol scope must be present before activation")
            scope = self._model_scope(market, symbol)
            prior_file_exists = self._active_models_path.is_file()
            prior_file_content = self._active_models_path.read_bytes() if prior_file_exists else None
            try:
                active_models = self._read_active_models()
                previous_entry = active_models.get(scope)
                previous = previous_entry.get("model_id") if isinstance(previous_entry, dict) else None
                if previous is not None:
                    if not isinstance(previous, str):
                        raise ValueError("active model ID is missing")
                    self.manifest(previous)
            except (OSError, json.JSONDecodeError, AttributeError, ValueError, FileNotFoundError) as exc:
                raise ValueError("active model registry is invalid and must be repaired before changing it") from exc
            changed_at = datetime.now().astimezone().isoformat()
            active_models[scope] = {"model_id": model_id, "changed_at": changed_at}
            self._write_json_atomic(self._active_models_path,
                                    {"schema_version": 1, "models": active_models})
            event = {"action": action, "model_id": model_id, "scope": scope, "previous_model_id": previous,
                     "changed_by": operator, "note": justification, "changed_at": changed_at}
            try:
                self._record_governance_event(event)
            except (OSError, ValueError):
                if not prior_file_exists:
                    self._active_models_path.unlink(missing_ok=True)
                else:
                    self._write_json_atomic(self._active_models_path,
                                            json.loads((prior_file_content or b"{}").decode("utf-8")))
                raise
            return event

    def governance_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._governance_lock:
            try:
                events = self._read_governance_events()
                return events[-max(1, min(limit, 200)):]
            except (OSError, ValueError, json.JSONDecodeError):
                return []

    def _record_governance_event(self, event: dict[str, Any]) -> None:
        events = self._read_governance_events()
        chained_event = dict(event)
        chained_event["previous_event_sha256"] = (
            str(events[-1]["event_sha256"]) if events else _GOVERNANCE_GENESIS_SHA256
        )
        chained_event["event_sha256"] = self._governance_event_sha256(chained_event)
        existing = self._audit_path.read_bytes() if self._audit_path.is_file() else b""
        line = (json.dumps(chained_event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=".governance-audit-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(existing)
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._audit_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".governance-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def train_compare(self, *, market: str, symbol: str,
                      sessions: dict[date, list[MinuteBar]], quant_config: QuantConfig = QuantConfig(),
                      label_horizon_bars: int, label_round_trip_cost_bps: Decimal,
                      dataset_sha256: str, dataset_provenance: dict[str, Any]) -> list[ModelTrainingResult]:
        market = market.upper()
        if market not in {"KR", "US"}:
            raise ValueError("market must be KR or US; train separate market-specific candidates")
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("a single instrument symbol is required for strategy backtests")
        if label_horizon_bars <= 0 or label_round_trip_cost_bps < 0:
            raise ValueError("label horizon and round-trip cost settings are invalid")
        minimum_label_cost = quant_config.minimum_round_trip_cost_bps
        if minimum_label_cost <= 0 or label_round_trip_cost_bps < minimum_label_cost:
            raise ValueError("label round-trip cost must cover modeled buy/sell fees, tax, and slippage")
        if not re.fullmatch(r"[a-f0-9]{64}", dataset_sha256):
            raise ValueError("dataset_sha256 must be a lowercase SHA-256 digest")
        provenance = validate_data_provenance(dataset_provenance, expected_sha256=dataset_sha256,
                                              purpose="model_training")
        # Derive every training row from the exact session mapping used for the
        # strategy backtest. Callers cannot pair samples from another dataset
        # with this dataset's reviewed provenance digest.
        samples = build_training_samples(
            sessions, market=market, horizon_bars=label_horizon_bars,
            round_trip_cost_bps=label_round_trip_cost_bps, quant_config=quant_config,
        )
        try:
            from lightgbm import LGBMClassifier
            from xgboost import XGBClassifier
            import joblib
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("Install the optional ai dependencies to train LightGBM and XGBoost models") from exc
        train, validation, test = _chronological_partitions(samples)
        if (len(train) < MIN_MODEL_TRAIN_SAMPLES
                or len(validation) < MIN_MODEL_VALIDATION_SAMPLES
                or len(test) < MIN_MODEL_TEST_SAMPLES):
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
        test_session_dates = {sample.session for sample in test}
        test_bars = {session: bars for session, bars in sessions.items() if session in test_session_dates}
        if set(test_bars) != test_session_dates:
            raise ValueError("every held-out sample session must have source bars for strategy backtesting")
        quant_backtest_test_metrics = _backtest_metrics([
            backtest_orb_vwap(market, normalized_symbol, test_bars[session], quant_config)
            for session in sorted(test_bars)
        ])
        training_config = {
            "symbol": normalized_symbol,
            "label_version": LABEL_VERSION,
            "backtest_version": BACKTEST_VERSION,
            "backtest_spread_model": BACKTEST_SPREAD_MODEL,
            "label_horizon_bars": label_horizon_bars,
            "label_round_trip_cost_bps": str(label_round_trip_cost_bps),
            "backtest_quantity_per_session": 1,
            "backtest_buy_commission_bps": str(quant_config.effective_buy_commission_bps),
            "backtest_sell_commission_bps": str(quant_config.effective_sell_commission_bps),
            "backtest_sell_tax_bps": str(quant_config.sell_tax_bps),
            "backtest_slippage_bps": str(quant_config.slippage_bps),
            "backtest_minimum_turnover": str(quant_config.minimum_turnover),
            "backtest_maximum_spread_bps": str(quant_config.maximum_spread_bps),
            "backtest_minimum_relative_volume": str(quant_config.minimum_relative_volume),
            "backtest_atr_stop_multiple": str(quant_config.atr_stop_multiple),
            "backtest_reward_risk_multiple": str(quant_config.reward_risk_multiple),
        }
        for name, model in candidates.items():
            model.fit(x_train, y_train)
            validation_metrics = _metrics(model, validation)
            test_metrics = _metrics(model, test)
            quant_baseline_test_metrics = _quant_baseline_metrics(test)
            def ai_entry_filter(history: list[MinuteBar]) -> bool:
                features = latest_features(history, market=market)
                probability = _positive_probability(model, features)
                return probability >= 0.5

            ai_quant_backtest_test_metrics = _backtest_metrics([
                backtest_orb_vwap(market, normalized_symbol, test_bars[session], quant_config,
                                  entry_filter=ai_entry_filter)
                for session in sorted(test_bars)
            ])
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
                "model_id": model_id, "market": market, "model_type": name, "artifact": artifact_name,
                "artifact_sha256": digest, "feature_names": list(FEATURE_NAMES), "feature_version": FEATURE_VERSION,
                "threshold": 0.5, "approved": False,
                "dataset_sha256": dataset_sha256,
                "data_provenance": provenance,
                "validation_metrics": validation_metrics, "test_metrics": test_metrics,
                "quant_baseline_test_metrics": quant_baseline_test_metrics,
                "quant_backtest_test_metrics": quant_backtest_test_metrics,
                "ai_quant_backtest_test_metrics": ai_quant_backtest_test_metrics,
                "training_config": training_config,
                "train_sessions": train_dates, "validation_sessions": validation_dates,
                "test_sessions": test_dates,
            }
            manifest_path = self.root / f"{model_id}.json"
            manifest_tmp = manifest_path.with_suffix(".json.tmp")
            manifest_tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            os.replace(manifest_tmp, manifest_path)
            results.append(ModelTrainingResult(model_id, market, name, 0.5, validation_metrics, test_metrics,
                                               quant_baseline_test_metrics, quant_backtest_test_metrics,
                                               ai_quant_backtest_test_metrics, training_config,
                                               train_dates, validation_dates,
                                               test_dates, digest))
        return results

    def evaluate_candidate(self, model_id: str, sessions: dict[date, list[MinuteBar]], *,
                           market: str, symbol: str, dataset_sha256: str,
                           dataset_provenance: dict[str, Any]) -> dict[str, Any]:
        """Attach independent, later-session evidence to a fixed unapproved candidate."""
        import joblib

        market = market.strip().upper()
        symbol = symbol.strip().upper()
        provenance = validate_data_provenance(
            dataset_provenance, expected_sha256=dataset_sha256, purpose="backtesting"
        )
        if not re.fullmatch(r"[a-f0-9]{64}", dataset_sha256):
            raise ValueError("dataset_sha256 must be a lowercase SHA-256 digest")
        if market not in {"KR", "US"}:
            raise ValueError("market must be KR or US")
        with self._governance_lock:
            metadata = self.manifest(model_id)
            if metadata.get("approved") is True:
                raise ValueError("approved models cannot be rewritten with new evaluation evidence")
            if metadata.get("heldout_evaluation") is not None:
                raise ValueError("candidate already has independent holdout evidence and cannot be re-evaluated")
            if metadata.get("market") != market or metadata.get("training_config", {}).get("symbol") != symbol:
                raise ValueError("evaluation market and symbol must match the candidate")
            if not self._supports_current_model_contract(metadata):
                raise ValueError("candidate feature schema is outdated")
            train_dates = set(metadata.get("train_sessions", []))
            validation_dates = set(metadata.get("validation_sessions", []))
            prior_test_dates = set(metadata.get("test_sessions", []))
            prior_dates = train_dates | validation_dates | prior_test_dates
            if not prior_dates or any(not isinstance(value, str) for value in prior_dates):
                raise ValueError("candidate training partitions are incomplete")
            try:
                prior_end = max(date.fromisoformat(value) for value in prior_dates)
            except ValueError as exc:
                raise ValueError("candidate training partitions must contain ISO dates") from exc
            if not sessions or min(sessions) <= prior_end:
                raise ValueError("independent holdout sessions must be later than all training data")
            if len(sessions) < MIN_MODEL_APPROVAL_TEST_SESSIONS:
                raise ValueError("at least five independent holdout sessions are required")
            requested_holdout_dates = {value.isoformat() for value in sessions}
            for event in self._read_governance_events():
                if event.get("action") != "evaluate_holdout":
                    continue
                event_market = event.get("market")
                event_symbol = event.get("symbol")
                if not isinstance(event_market, str) or not isinstance(event_symbol, str):
                    prior_id = event.get("model_id")
                    if not isinstance(prior_id, str):
                        raise ValueError("prior holdout audit cannot be scoped; refusing to reuse holdout data")
                    prior_metadata = self.manifest(prior_id)
                    event_market = prior_metadata.get("market")
                    event_symbol = prior_metadata.get("training_config", {}).get("symbol")
                prior_dates = event.get("test_sessions")
                if event_market == market and event_symbol == symbol:
                    if (not isinstance(prior_dates, list)
                            or any(not isinstance(value, str) for value in prior_dates)):
                        raise ValueError("prior holdout audit has invalid session dates; refusing to reuse data")
                    try:
                        prior_dates = {date.fromisoformat(value).isoformat() for value in prior_dates}
                    except ValueError as exc:
                        raise ValueError("prior holdout audit has invalid session dates; refusing to reuse data") from exc
                    if requested_holdout_dates.intersection(prior_dates):
                        raise ValueError("holdout sessions have already been used for this market and symbol")
            selection = self.select_candidate_for_holdout(market, symbol, sessions)
            if model_id != selection["model_id"]:
                raise ValueError("only the validation-selected candidate may consume holdout sessions")
            config_data = metadata["training_config"]
            quant_config = QuantConfig(
                commission_bps=Decimal("0"),
                buy_commission_bps=Decimal(str(config_data["backtest_buy_commission_bps"])),
                sell_commission_bps=Decimal(str(config_data["backtest_sell_commission_bps"])),
                sell_tax_bps=Decimal(str(config_data["backtest_sell_tax_bps"])),
                slippage_bps=Decimal(str(config_data["backtest_slippage_bps"])),
                minimum_turnover=Decimal(str(config_data["backtest_minimum_turnover"])),
                maximum_spread_bps=Decimal(str(config_data["backtest_maximum_spread_bps"])),
                minimum_relative_volume=Decimal(str(config_data["backtest_minimum_relative_volume"])),
                atr_stop_multiple=Decimal(str(config_data["backtest_atr_stop_multiple"])),
                reward_risk_multiple=Decimal(str(config_data["backtest_reward_risk_multiple"])),
            )
            samples = build_training_samples(
                sessions, market=market, horizon_bars=int(config_data["label_horizon_bars"]),
                round_trip_cost_bps=Decimal(str(config_data["label_round_trip_cost_bps"])),
                quant_config=quant_config,
            )
            actual_dates = {sample.session for sample in samples}
            if len(actual_dates) < MIN_MODEL_APPROVAL_TEST_SESSIONS or actual_dates != set(sessions):
                raise ValueError("all independent holdout sessions must produce valid samples")
            model = joblib.load(self.root / metadata["artifact"])
            test_metrics = _metrics(model, samples)

            def ai_entry_filter(history: list[MinuteBar]) -> bool:
                features = latest_features(history, market=market)
                probability = _positive_probability(model, features)
                return probability >= float(metadata["threshold"])

            quant_metrics = _backtest_metrics([
                backtest_orb_vwap(market, symbol, sessions[session], quant_config)
                for session in sorted(sessions)
            ])
            ai_metrics = _backtest_metrics([
                backtest_orb_vwap(market, symbol, sessions[session], quant_config,
                                  entry_filter=ai_entry_filter)
                for session in sorted(sessions)
            ])
            test_dates = tuple(sorted(value.isoformat() for value in actual_dates))
            updated = json.loads(json.dumps(metadata))
            updated["test_metrics"] = test_metrics
            updated["quant_baseline_test_metrics"] = _quant_baseline_metrics(samples)
            updated["quant_backtest_test_metrics"] = quant_metrics
            updated["ai_quant_backtest_test_metrics"] = ai_metrics
            updated["test_sessions"] = test_dates
            updated["heldout_evaluation"] = {
                "market": market,
                "symbol": symbol,
                "candidate_selection": selection,
                "dataset_sha256": dataset_sha256,
                "data_provenance": provenance,
                "sessions": list(test_dates),
                "model_artifact_sha256": metadata["artifact_sha256"],
                "threshold": metadata["threshold"],
                "evaluated_at": datetime.now().astimezone().isoformat(),
            }
            validate_data_provenance(
                updated["heldout_evaluation"]["data_provenance"],
                expected_sha256=dataset_sha256, purpose="backtesting",
            )
            self._write_json_atomic(self.root / f"{model_id}.json", updated)
            try:
                self._record_governance_event({
                    "action": "evaluate_holdout", "model_id": model_id,
                    "market": market, "symbol": symbol,
                    "candidate_selection": selection,
                    "dataset_sha256": dataset_sha256,
                    "data_provenance": provenance,
                    "artifact_sha256": metadata["artifact_sha256"],
                    "threshold": metadata["threshold"],
                    "test_sessions": list(test_dates),
                    "evaluated_at": updated["heldout_evaluation"]["evaluated_at"],
                    "quant_backtest_test_metrics": quant_metrics,
                    "ai_quant_backtest_test_metrics": ai_metrics,
                })
            except Exception:
                self._write_json_atomic(self.root / f"{model_id}.json", metadata)
                raise
            return {
                "model_id": model_id, "approved": False,
                "test_sessions": list(test_dates), "test_metrics": test_metrics,
                "quant_backtest_test_metrics": quant_metrics,
                "ai_quant_backtest_test_metrics": ai_metrics,
                "approval_criteria_met": (
                    int(ai_metrics["trade_count"]) >= MIN_MODEL_APPROVAL_TEST_TRADES
                    and Decimal(ai_metrics["net_pnl"]) > 0
                    and Decimal(ai_metrics["net_pnl"]) > Decimal(quant_metrics["net_pnl"])
                ),
            }

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

    def predict_preview(self, model_id: str, bars: list[MinuteBar], *, market: str, symbol: str) -> dict[str, Any]:
        try:
            import joblib
        except ImportError as exc:
            raise RuntimeError("Install the optional ai dependencies to run inference") from exc
        metadata = self.manifest(model_id)
        if market.strip().upper() != metadata.get("market"):
            raise ValueError("model market scope does not match the requested market")
        requested_symbol = symbol.strip().upper()
        trained_symbol = str(metadata.get("training_config", {}).get("symbol", "")).strip().upper()
        if not requested_symbol or trained_symbol != requested_symbol:
            raise ValueError("model symbol scope does not match the requested symbol")
        if not self._supports_current_model_contract(metadata):
            raise ValueError("model feature schema is outdated and must be retrained")
        artifact = self.root / metadata["artifact"]
        model = joblib.load(artifact)
        features = latest_features(bars, market=market)
        probability = _positive_probability(model, features)
        raw_threshold = metadata.get("threshold")
        if (isinstance(raw_threshold, bool) or not isinstance(raw_threshold, (int, float))
                or not math.isfinite(float(raw_threshold)) or not 0 <= float(raw_threshold) <= 1):
            raise ValueError("model threshold is invalid")
        threshold = float(raw_threshold)
        return {
            "model_id": model_id,
            "market": metadata["market"],
            "model_type": metadata["model_type"],
            "probability_positive": probability,
            "threshold": threshold,
            "signal": "BUY_CANDIDATE" if probability >= threshold else "HOLD",
            "approved": bool(metadata.get("approved", False)),
            "executable": False,
        }

