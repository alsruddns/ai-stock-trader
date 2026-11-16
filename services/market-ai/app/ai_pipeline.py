"""Leakage-aware LightGBM/XGBoost training and local model registry."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

from .data_provenance import (MIN_MODEL_TRAINING_SESSIONS, validate_data_provenance,
                              validate_model_session_integrity, validate_model_training_coverage)
from .market_calendar import exchange_continuous_session_window, exchange_session_schedule
from .quant import (MinuteBar, QuantConfig, _atr, _ema, _vwap,
                    _orb_vwap_entry_candidate_validated, _validate_session, backtest_orb_vwap,
                    exchange_session_date)

FEATURE_NAMES = (
    "return_1m", "return_3m", "return_5m", "close_vwap_pct", "ema9_gap_pct",
    "ema20_gap_pct", "atr14_pct", "relative_volume20", "session_progress",
    "orb_breakout_distance_bps",
)
FEATURE_VERSION = 5
LABEL_VERSION = 4
BACKTEST_VERSION = 6
BACKTEST_SPREAD_MODEL = "known_prior_bar_half_spread_entry_exit_v2"
VALIDATION_THRESHOLD_SELECTION_METHOD = "validation_candidate_positive_net_mean_v1"
DIAGNOSTIC_THRESHOLD_SELECTION_METHOD = "diagnostic_fixed_0_5"
MIN_MODEL_APPROVAL_TEST_SESSIONS = 5
MIN_MODEL_APPROVAL_TEST_TRADES = 5
MIN_MODEL_HOLDOUT_SELECTION_VALIDATION_SESSIONS = 3
MIN_MODEL_VALIDATION_QUANT_CANDIDATE_SAMPLES = 10
MIN_MODEL_VALIDATION_AI_CANDIDATE_TRADES = 5
MIN_MODEL_VALIDATION_AI_CANDIDATE_SESSIONS = 3
MIN_MODEL_TRAIN_SAMPLES = 30
MIN_MODEL_VALIDATION_SAMPLES = 10
MIN_MODEL_TEST_SAMPLES = 10
_REGISTRY_LOCKS_GUARD = threading.Lock()
_REGISTRY_LOCKS: dict[Path, tuple[threading.RLock, threading.local]] = {}
_GOVERNANCE_GENESIS_SHA256 = "0" * 64


_MARKETS = {"KR", "US"}


class ValidationThresholdSelectionError(ValueError):
    """A safe, aggregate explanation for a validation-threshold rejection."""

    def __init__(self, diagnostics: dict[str, Any]) -> None:
        self.diagnostics = diagnostics
        super().__init__(
            "validation quant candidates have no positive-net AI threshold with at least "
            f"{MIN_MODEL_VALIDATION_AI_CANDIDATE_TRADES} predictions across "
            f"{MIN_MODEL_VALIDATION_AI_CANDIDATE_SESSIONS} sessions"
        )


class NoProfitableValidationThresholdError(ValueError):
    """All model families failed the fixed validation profitability gate."""

    def __init__(self, rejections: list[dict[str, Any]]) -> None:
        self.diagnostics = rejections
        super().__init__(
            "validation quant candidates have no positive-net AI threshold with at least "
            f"{MIN_MODEL_VALIDATION_AI_CANDIDATE_TRADES} predictions across "
            f"{MIN_MODEL_VALIDATION_AI_CANDIDATE_SESSIONS} sessions for any model family "
            "(no_profitable_validation_ai_threshold)"
        )


def _validated_session_dates(sessions: dict[date, list[MinuteBar]]) -> list[date]:
    """Require unambiguous exchange-session keys before ordering holdout data."""
    if not isinstance(sessions, dict) or not sessions:
        raise ValueError("independent holdout sessions are required")
    if any(type(session) is not date for session in sessions):
        raise ValueError("holdout session keys must be calendar dates")
    return sorted(sessions)


def _meets_holdout_approval_criteria(ai_metrics: dict[str, Any], quant_metrics: dict[str, Any]) -> bool:
    """Check approval metrics without truncating fractional or non-finite trade counts."""
    try:
        raw_trade_count = ai_metrics["trade_count"]
        if isinstance(raw_trade_count, bool):
            return False
        trade_count = Decimal(str(raw_trade_count))
        ai_net = Decimal(str(ai_metrics["net_pnl"]))
        quant_net = Decimal(str(quant_metrics["net_pnl"]))
        return (trade_count.is_finite() and trade_count == trade_count.to_integral_value()
                and trade_count >= MIN_MODEL_APPROVAL_TEST_TRADES
                and ai_net.is_finite() and quant_net.is_finite()
                and ai_net > 0 and ai_net > quant_net)
    except (ArithmeticError, KeyError, TypeError, ValueError):
        return False


def _metrics_cover_both_outcome_classes(metrics: Any) -> bool:
    """Require evaluation labels to contain positive and negative examples."""
    if not isinstance(metrics, dict):
        return False
    value = metrics.get("positive_label_rate")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return False
    return 0 < float(value) < 1


def _is_finite_integer(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(float(value)) and float(value).is_integer())


def _validation_quant_candidate_reasons(samples: list[TrainingSample]) -> list[str]:
    """Require enough ORB/VWAP entry candidates to assess the runtime AI filter."""
    candidates = [sample for sample in samples if sample.quant_candidate]
    reasons = []
    if len(candidates) < MIN_MODEL_VALIDATION_QUANT_CANDIDATE_SAMPLES:
        reasons.append("insufficient_validation_quant_candidate_samples")
    if len({sample.session for sample in candidates}) < MIN_MODEL_HOLDOUT_SELECTION_VALIDATION_SESSIONS:
        reasons.append("insufficient_validation_quant_candidate_sessions")
    if len({sample.label for sample in candidates}) < 2:
        reasons.append("validation_quant_candidates_require_both_outcome_classes")
    return reasons


def _session_progress(
    timestamp: datetime, market: str,
    continuous_session_window: tuple[datetime, datetime] | None = None,
) -> Decimal:
    """Fraction of the local regular session elapsed, clamped to [0, 1]."""
    normalized_market = market.strip().upper()
    if normalized_market not in _MARKETS:
        raise ValueError("market must be KR or US")
    window = continuous_session_window or exchange_continuous_session_window(normalized_market, timestamp)
    if window is None:
        raise ValueError("timestamp must belong to an official market session")
    opened, closed = (bound.astimezone(timezone.utc) for bound in window)
    instant = timestamp.astimezone(timezone.utc)
    seconds = (instant - opened).total_seconds()
    duration_seconds = (closed - opened).total_seconds()
    return min(Decimal(1), max(Decimal(0), Decimal(seconds) / Decimal(duration_seconds)))


def _ema_series(bars: Sequence[MinuteBar], period: int) -> list[Decimal | None]:
    """Calculate each prefix EMA once using the same seed and recurrence as quant._ema."""
    values: list[Decimal | None] = [None] * len(bars)
    if period <= 0 or len(bars) < period:
        return values
    alpha = Decimal(2) / Decimal(period + 1)
    result = sum((bar.close for bar in bars[:period]), Decimal(0)) / Decimal(period)
    values[period - 1] = result
    for index in range(period, len(bars)):
        result = bars[index].close * alpha + result * (Decimal(1) - alpha)
        values[index] = result
    return values


def _vwap_series(bars: Sequence[MinuteBar]) -> list[Decimal]:
    """Calculate each cumulative VWAP in one pass, preserving the quant formula."""
    result: list[Decimal] = []
    value_volume = Decimal(0)
    volume = 0
    for bar in bars:
        typical_price = (bar.high + bar.low + bar.close) / Decimal(3)
        value_volume += typical_price * bar.volume
        volume += bar.volume
        result.append(value_volume / Decimal(volume) if volume else bar.close)
    return result


def _atr_series(bars: Sequence[MinuteBar], period: int) -> list[Decimal | None]:
    """Calculate ATR for every prefix with a bounded rolling sum."""
    if period <= 0:
        raise ValueError("ATR period must be positive")
    true_ranges: list[Decimal] = []
    result: list[Decimal | None] = []
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1].close if index else bar.close
        true_ranges.append(max(
            bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close),
        ))
        window = true_ranges[max(0, index - period + 1):index + 1]
        result.append(sum(window, Decimal(0)) / Decimal(len(window)))
    return result


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
    validation_metrics: dict[str, float | None]
    test_metrics: dict[str, float | None]
    quant_baseline_test_metrics: dict[str, float]
    quant_backtest_test_metrics: dict[str, Any]
    ai_quant_backtest_test_metrics: dict[str, Any]
    training_config: dict[str, Any]
    train_sessions: tuple[str, ...]
    validation_sessions: tuple[str, ...]
    test_sessions: tuple[str, ...]
    artifact_sha256: str
    training_diagnostics: dict[str, Any] = field(default_factory=dict)


def build_training_samples(
    sessions: dict[date, list[MinuteBar]], *, market: str, horizon_bars: int = 5,
    round_trip_cost_bps: Decimal = Decimal("10"), warmup_bars: int = 20,
    quant_config: QuantConfig = QuantConfig(),
    exchange_schedule: Sequence[tuple[date, datetime, datetime]] | None = None,
) -> list[TrainingSample]:
    """Create samples using features available at t and an executable forward label.

    Labels use configured commissions, tax, and slippage. Historical OHLCV does
    not contain a trustworthy spread, so spread is excluded from both features
    and labels; live execution continues to enforce a separately observed quote
    spread gate. Sessions are processed independently, so labels never cross a
    close. Caller owns data licensing and corporate-action/symbol normalization.
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
    required = max(warmup_bars, 21, 15, 10, quant_config.opening_range_bars)
    if not sessions:
        return samples
    verified_schedule = exchange_schedule or exchange_session_schedule(
        normalized_market, min(sessions), max(sessions),
    )
    continuous_windows = {
        session_date: (
            opened,
            closed - timedelta(minutes=10) if normalized_market == "KR" else closed,
        )
        for session_date, opened, closed in verified_schedule
    }
    for session, bars in sorted(sessions.items()):
        if not bars:
            continue
        if any(exchange_session_date(item.timestamp, normalized_market) != session for item in bars):
            raise ValueError("bar date does not match its session key")
        continuous_window = continuous_windows.get(session)
        if continuous_window is None:
            raise ValueError("training session is not present in the exchange calendar")
        _validate_session(
            bars, normalized_market, require_complete=True,
            continuous_session_window=continuous_window,
        )
        vwap_values = _vwap_series(bars)
        ema_periods = {9, 20, quant_config.ema_period}
        ema_values = {period: _ema_series(bars, period) for period in ema_periods}
        atr_periods = {14, quant_config.atr_period}
        atr_values = {period: _atr_series(bars, period) for period in atr_periods}
        opening_range_high = max(bar.high for bar in bars[:quant_config.opening_range_bars])
        for index in range(required - 1, len(bars) - horizon_bars):
            current = bars[index]
            history = bars[: index + 1]
            prior = bars[max(0, index - 20):index]
            mean_volume = Decimal(sum(item.volume for item in prior)) / len(prior) if prior else Decimal(0)
            volume_ratio = Decimal(current.volume) / mean_volume if mean_volume else Decimal(0)
            entry = bars[index + 1]
            close = current.close
            returns = [
                Decimal(0) if index < period else close / bars[index - period].close - Decimal(1)
                for period in (1, 3, 5)
            ]
            vwap = vwap_values[index]
            ema9 = ema_values[9][index]
            ema20 = ema_values[20][index]
            atr14 = atr_values[14][index]
            if ema9 is None or ema20 is None or atr14 is None:
                continue
            atr_pct = atr14 / close
            session_progress = _session_progress(
                current.timestamp, normalized_market, continuous_window,
            )
            orb_breakout_distance_bps = (
                close / opening_range_high - Decimal(1)
            ) * Decimal(10_000)
            feature_values = (
                *returns,
                close / vwap - Decimal(1) if vwap else Decimal(0),
                close / ema9 - Decimal(1),
                close / ema20 - Decimal(1),
                atr_pct,
                volume_ratio,
                session_progress,
                orb_breakout_distance_bps,
            )
            if any(not value.is_finite() for value in feature_values):
                continue
            future = bars[index + horizon_bars]
            future_return = future.close / entry.open - Decimal(1)
            forward_net_return_bps = future_return * Decimal(10_000) - round_trip_cost_bps
            label = int(forward_net_return_bps > 0)
            quant_candidate = _orb_vwap_entry_candidate_validated(
                history, quant_config, require_spread=False,
                vwap_value=vwap,
                ema_value=ema_values[quant_config.ema_period][index],
                atr_value=atr_values[quant_config.atr_period][index],
                opening_range_high=opening_range_high,
            )
            samples.append(TrainingSample(session, current.timestamp.isoformat(),
                                          tuple(float(value) for value in feature_values), label, quant_candidate,
                                          float(forward_net_return_bps)))
    return samples


def latest_features(
    bars: list[MinuteBar], *, market: str, minimum_bars: int = 21,
    quant_config: QuantConfig = QuantConfig(),
) -> tuple[float, ...]:
    """Build the current-bar feature vector without reading a future observation."""
    required_bars = max(21, minimum_bars, quant_config.opening_range_bars)
    if len(bars) < required_bars:
        raise ValueError(f"at least {required_bars} bars are required for inference")
    normalized_market = market.strip().upper()
    if normalized_market not in _MARKETS:
        raise ValueError("market must be KR or US")
    _validate_session(bars, normalized_market)
    current = bars[-1]
    index = len(bars) - 1
    prior = bars[max(0, index - 20):index]
    mean_volume = Decimal(sum(item.volume for item in prior)) / len(prior) if prior else Decimal(0)
    volume_ratio = Decimal(current.volume) / mean_volume if mean_volume else Decimal(0)
    opening_range_high = max(item.high for item in bars[:quant_config.opening_range_bars])
    close = current.close
    features = (
        *(Decimal(0) if index < period else close / bars[index - period].close - Decimal(1) for period in (1, 3, 5)),
        close / _vwap(bars) - Decimal(1),
        close / _ema([item.close for item in bars], 9) - Decimal(1),
        close / _ema([item.close for item in bars], 20) - Decimal(1),
        _atr(bars, 14) / close,
        volume_ratio,
        _session_progress(current.timestamp, normalized_market),
        (close / opening_range_high - Decimal(1)) * Decimal(10_000),
    )
    return tuple(float(value) for value in features)


def _feature_quant_config(training_config: dict[str, Any]) -> QuantConfig:
    opening_range_bars = training_config.get("backtest_opening_range_bars")
    if (isinstance(opening_range_bars, bool) or not isinstance(opening_range_bars, int)
            or opening_range_bars <= 0):
        raise ValueError("model opening-range feature configuration is invalid")
    return QuantConfig(opening_range_bars=opening_range_bars)


def predict_probability_series(model: Any, bars: list[MinuteBar], *, market: str,
                               minimum_bars: int = 21,
                               quant_config: QuantConfig = QuantConfig()) -> dict[str, float]:
    """Score each bar using only data through that bar close for offline engine parity."""
    if not isinstance(minimum_bars, int) or isinstance(minimum_bars, bool) or minimum_bars < 21:
        raise ValueError("minimum_bars must be an integer of at least 21")
    if not bars:
        raise ValueError("bars are required for model scoring")
    _validate_session(bars, market.strip().upper())
    scores: dict[str, float] = {}
    for index in range(minimum_bars - 1, len(bars)):
        history = bars[:index + 1]
        features = latest_features(history, market=market, minimum_bars=minimum_bars,
                                   quant_config=quant_config)
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
    candidates = [sample for sample in samples if sample.quant_candidate]
    candidate_positive = sum(sample.label == 1 for sample in candidates)
    candidate_negative = sum(sample.label == 0 for sample in candidates)
    candidate_sessions = {sample.session for sample in candidates}
    session_counts = {
        session.isoformat(): {
            "sample_count": 0, "positive": 0, "negative": 0,
            "candidate_sample_count": 0, "candidate_positive": 0, "candidate_negative": 0,
        }
        for session in dates
    }
    for sample in samples:
        counts = session_counts[sample.session.isoformat()]
        counts["sample_count"] += 1
        counts["positive" if sample.label == 1 else "negative"] += 1
        if sample.quant_candidate:
            counts["candidate_sample_count"] += 1
            counts["candidate_positive" if sample.label == 1 else "candidate_negative"] += 1
    result: dict[str, Any] = {
        "ready": False,
        "sample_count": len(samples),
        "session_count": len(dates),
        "positive_label_count": positive,
        "negative_label_count": negative,
        "candidate_sample_count": len(candidates),
        "candidate_coverage": len(candidates) / len(samples) if samples else 0.0,
        "candidate_positive_label_count": candidate_positive,
        "candidate_negative_label_count": candidate_negative,
        "candidate_session_count": len(candidate_sessions),
        "session_class_counts": [
            {"session_date": session, **counts} for session, counts in session_counts.items()
        ],
        "partition_sample_counts": None,
        "partition_session_counts": None,
        "partition_class_counts": None,
        "partition_candidate_sample_counts": None,
        "partition_candidate_session_counts": None,
        "partition_candidate_class_counts": None,
        "partition_scope": None,
        "partition_diagnostic_reason": None,
        "reasons": [],
    }
    if len(dates) < 3:
        result["partition_diagnostic_reason"] = "at_least_three_distinct_sessions_required"
        result["reasons"] = ["insufficient_eligible_sessions"]
        return result
    try:
        train, validation, test = _chronological_partitions(samples)
    except ValueError:
        result["partition_diagnostic_reason"] = "chronological_partitioning_failed"
        result["reasons"] = ["chronological_partitioning_failed"]
        return result
    result["partition_scope"] = (
        "production" if len(dates) >= MIN_MODEL_TRAINING_SESSIONS else "diagnostic"
    )
    partitions = {"train": train, "validation": validation, "test": test}
    result["partition_sample_counts"] = {name: len(rows) for name, rows in partitions.items()}
    result["partition_session_counts"] = {
        name: len({sample.session for sample in rows}) for name, rows in partitions.items()
    }
    result["partition_class_counts"] = {
        name: {
            "positive": sum(sample.label == 1 for sample in rows),
            "negative": sum(sample.label == 0 for sample in rows),
        }
        for name, rows in partitions.items()
    }
    candidate_partitions = {
        name: [sample for sample in rows if sample.quant_candidate]
        for name, rows in partitions.items()
    }
    result["partition_candidate_sample_counts"] = {
        name: len(rows) for name, rows in candidate_partitions.items()
    }
    result["partition_candidate_session_counts"] = {
        name: len({sample.session for sample in rows})
        for name, rows in candidate_partitions.items()
    }
    result["partition_candidate_class_counts"] = {
        name: {
            "positive": sum(sample.label == 1 for sample in rows),
            "negative": sum(sample.label == 0 for sample in rows),
        }
        for name, rows in candidate_partitions.items()
    }
    if len(dates) < MIN_MODEL_TRAINING_SESSIONS:
        result["reasons"] = ["insufficient_eligible_sessions"]
        return result
    reasons = []
    if (len(train) < MIN_MODEL_TRAIN_SAMPLES
            or len(validation) < MIN_MODEL_VALIDATION_SAMPLES
            or len(test) < MIN_MODEL_TEST_SAMPLES):
        reasons.append("insufficient_samples_per_partition")
    if len({sample.label for sample in train}) < 2:
        reasons.append("training_partition_requires_both_outcome_classes")
    if len({sample.label for sample in validation}) < 2:
        reasons.append("validation_partition_requires_both_outcome_classes")
    if len({sample.label for sample in test}) < 2:
        reasons.append("test_partition_requires_both_outcome_classes")
    reasons.extend(_validation_quant_candidate_reasons(validation))
    result["reasons"] = reasons
    result["ready"] = not reasons
    return result


def require_approval_ready_training(samples: list[TrainingSample]) -> dict[str, Any]:
    """Fail closed when a production training invocation cannot meet approval gates."""
    readiness = assess_training_samples(samples)
    if not readiness["ready"]:
        reasons = ", ".join(readiness["reasons"])
        raise ValueError("approval-ready model training is not ready: " + reasons)
    return readiness


def _select_validation_threshold(
    model: Any, samples: list[TrainingSample], *, allow_diagnostic_fallback: bool = False,
) -> float:
    """Choose a fixed threshold only when validation candidates have positive net returns."""
    candidates = [sample for sample in samples if sample.quant_candidate]
    if not candidates:
        if allow_diagnostic_fallback:
            return 0.5
        raise ValueError("validation quant candidates are required for AI threshold selection")
    probabilities = _positive_probabilities(model, [sample.features for sample in candidates])
    if len(candidates) != len(probabilities):
        raise ValueError("validation candidate probabilities are incomplete")
    if any(sample.forward_net_return_bps is None for sample in candidates):
        raise ValueError("validation candidate net returns are incomplete")

    best_threshold: float | None = None
    best_score: tuple[float, float, float] | None = None
    thresholds = sorted({float(value) for value in probabilities})
    eligible_threshold_count = 0
    positive_net_threshold_count = 0
    max_selected_predictions = 0
    max_selected_sessions = 0
    best_eligible_mean_net_return_bps: float | None = None
    for threshold in thresholds:
        selected = [
            (sample, float(probability))
            for sample, probability in zip(candidates, probabilities)
            if float(probability) >= threshold
        ]
        selected_session_count = len({sample.session for sample, _ in selected})
        max_selected_predictions = max(max_selected_predictions, len(selected))
        max_selected_sessions = max(max_selected_sessions, selected_session_count)
        if (len(selected) < MIN_MODEL_VALIDATION_AI_CANDIDATE_TRADES
                or selected_session_count < MIN_MODEL_VALIDATION_AI_CANDIDATE_SESSIONS):
            continue
        eligible_threshold_count += 1
        mean_net_return = sum(
            float(sample.forward_net_return_bps) for sample, _ in selected
        ) / len(selected)
        if (best_eligible_mean_net_return_bps is None
                or mean_net_return > best_eligible_mean_net_return_bps):
            best_eligible_mean_net_return_bps = mean_net_return
        if mean_net_return <= 0:
            continue
        positive_net_threshold_count += 1
        # Choose on validation-only, cost-adjusted forward returns. A candidate
        # with no profitable validation threshold cannot enter holdout review.
        score = (mean_net_return, float(len(selected)), threshold)
        if best_score is None or score > best_score:
            best_score = score
            best_threshold = threshold

    if best_threshold is None:
        if allow_diagnostic_fallback:
            return 0.5
        raise ValidationThresholdSelectionError({
            "validation_quant_candidate_count": len(candidates),
            "validation_quant_candidate_session_count": len({sample.session for sample in candidates}),
            "validation_thresholds_evaluated": len(thresholds),
            "validation_eligible_threshold_count": eligible_threshold_count,
            "validation_positive_net_threshold_count": positive_net_threshold_count,
            "validation_max_selected_predictions": max_selected_predictions,
            "validation_max_selected_sessions": max_selected_sessions,
            "validation_best_eligible_mean_net_return_bps": best_eligible_mean_net_return_bps,
            "minimum_validation_trades": MIN_MODEL_VALIDATION_AI_CANDIDATE_TRADES,
            "minimum_validation_sessions": MIN_MODEL_VALIDATION_AI_CANDIDATE_SESSIONS,
        })
    return best_threshold


def _metrics(model: Any, samples: list[TrainingSample], *, threshold: float = 0.5) -> dict[str, float | None]:
    from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score

    x = [sample.features for sample in samples]
    y = [sample.label for sample in samples]
    probabilities = _positive_probabilities(model, x)
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("model decision threshold must be finite and within [0, 1]")
    predictions = (probabilities >= threshold).astype(int)
    metrics = {"accuracy": float(accuracy_score(y, predictions)), "brier": float(brier_score_loss(y, probabilities))}
    actual_positive = sum(label == 1 for label in y)
    actual_negative = sum(label == 0 for label in y)
    predicted_positive = int(predictions.sum())
    true_positive = sum(label == 1 and prediction == 1 for label, prediction in zip(y, predictions))
    true_negative = sum(label == 0 and prediction == 0 for label, prediction in zip(y, predictions))
    positive_recall = true_positive / actual_positive if actual_positive else None
    specificity = true_negative / actual_negative if actual_negative else None
    metrics["positive_label_rate"] = actual_positive / len(y) if y else None
    metrics["predicted_positive_rate"] = predicted_positive / len(y) if y else None
    metrics["positive_precision"] = true_positive / predicted_positive if predicted_positive else None
    metrics["positive_recall"] = positive_recall
    metrics["specificity"] = specificity
    metrics["balanced_accuracy"] = (
        (positive_recall + specificity) / 2
        if positive_recall is not None and specificity is not None else None
    )
    # ROC-AUC is undefined for a single-class evaluation set. Keep it unavailable
    # instead of emitting 0.5, which looks like a measured neutral result.
    metrics["roc_auc"] = float(roc_auc_score(y, probabilities)) if len(set(y)) == 2 else None
    quant_candidates = [
        (sample, float(probability)) for sample, probability in zip(samples, probabilities)
        if sample.quant_candidate
    ]
    quant_candidate_labels = [sample.label for sample, _ in quant_candidates]
    metrics["quant_candidate_brier"] = (
        float(brier_score_loss(quant_candidate_labels, [probability for _, probability in quant_candidates]))
        if quant_candidates and len(set(quant_candidate_labels)) == 2 else None
    )
    metrics["quant_candidate_sample_count"] = float(len(quant_candidates))
    metrics["quant_candidate_session_count"] = float(len({sample.session for sample, _ in quant_candidates}))
    metrics["quant_candidate_positive_label_count"] = float(sum(quant_candidate_labels))
    metrics["quant_candidate_negative_label_count"] = float(len(quant_candidate_labels) - sum(quant_candidate_labels))
    # P5 runs inference only after ORB/VWAP passes, so evaluate the same hybrid candidate here.
    predicted_candidates = [
        sample for sample, prediction in zip(samples, predictions)
        if prediction == 1 and sample.quant_candidate
    ]
    selected_net_returns = [
        sample.forward_net_return_bps for sample in predicted_candidates
        if sample.forward_net_return_bps is not None
    ]
    metrics["candidate_count"] = float(len(predicted_candidates))
    metrics["candidate_coverage"] = float(len(predicted_candidates) / len(samples))
    metrics["candidate_positive_rate"] = (
        float(sum(sample.label for sample in predicted_candidates) / len(predicted_candidates))
        if predicted_candidates else None
    )
    metrics["candidate_mean_forward_net_bps"] = (
        float(sum(selected_net_returns) / len(selected_net_returns))
        if predicted_candidates and len(selected_net_returns) == len(predicted_candidates) else None
    )
    return metrics


def _quant_baseline_metrics(samples: list[TrainingSample]) -> dict[str, float | None]:
    selected = [sample for sample in samples if sample.quant_candidate]
    candidates = [sample.label for sample in selected]
    net_returns = [sample.forward_net_return_bps for sample in selected if sample.forward_net_return_bps is not None]
    return {
        "candidate_count": float(len(candidates)),
        "candidate_coverage": float(len(candidates) / len(samples)) if samples else 0.0,
        "candidate_positive_rate": float(sum(candidates) / len(candidates)) if candidates else None,
        "candidate_mean_forward_net_bps": (
            float(sum(net_returns) / len(net_returns))
            if candidates and len(net_returns) == len(candidates) else None
        ),
    }


def _backtest_metrics(results: list[Any]) -> dict[str, Any]:
    trades = [trade for result in results for trade in result.trades]
    net_pnl = sum((result.net_pnl for result in results), Decimal(0))
    return {
        "session_count": float(len(results)),
        "signal_count": float(sum(result.signals for result in results)),
        "trade_count": float(len(trades)),
        "winning_trades": float(sum(trade.net_pnl > 0 for trade in trades)),
        "win_rate": float(sum(trade.net_pnl > 0 for trade in trades) / len(trades)) if trades else None,
        "gross_pnl": str(sum((result.gross_pnl for result in results), Decimal(0))),
        "estimated_costs": str(sum((result.costs for result in results), Decimal(0))),
        "net_pnl": str(net_pnl),
        "average_trade_net_pnl": str(net_pnl / len(trades)) if trades else None,
    }


class LocalModelRegistry:
    """Stores model artifacts outside source control; newly trained models stay unapproved."""

    def __init__(self, root: str | Path = ".local/models"):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with _REGISTRY_LOCKS_GUARD:
            state = _REGISTRY_LOCKS.get(self.root)
            if state is None:
                state = (threading.RLock(), threading.local())
                _REGISTRY_LOCKS[self.root] = state
            self._governance_lock, self._governance_local = state

    @contextmanager
    def _governance_guard(self):
        """Serialize registry governance across threads and separate processes."""
        with self._governance_lock:
            depth = getattr(self._governance_local, "depth", 0)
            if depth:
                self._governance_local.depth = depth + 1
                try:
                    yield
                finally:
                    self._governance_local.depth -= 1
                return

            lock_path = self.root / ".governance.lock"
            with lock_path.open("a+b") as lock_file:
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    deadline = time.monotonic() + 30
                    while True:
                        try:
                            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise TimeoutError("timed out waiting for model registry governance lock")
                            time.sleep(0.05)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                self._governance_local.depth = 1
                try:
                    yield
                finally:
                    self._governance_local.depth = 0
                    if os.name == "nt":
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

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
                holdout_evaluation_verified = self._has_durable_holdout_evaluation(metadata)
                heldout_evaluation = metadata.get("heldout_evaluation")
                holdout_evaluation_recorded = heldout_evaluation is not None
                heldout_evaluation = heldout_evaluation if isinstance(heldout_evaluation, dict) else {}
                holdout_evaluation_status = (
                    "NOT_RUN" if not holdout_evaluation_recorded
                    else "VERIFIED" if holdout_evaluation_verified
                    else "UNVERIFIED"
                )
                approval_evidence_verified = (
                    self._has_valid_approval_evidence(metadata)
                    and holdout_evaluation_verified
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
                    "holdout_evaluation_status": holdout_evaluation_status,
                    "threshold_selection_method": metadata.get("threshold_selection_method"),
                    "approved": approved,
                    "active": metadata["model_id"] in active_model_ids and approved and model_compatible,
                    "artifact_sha256": metadata["artifact_sha256"],
                    "dataset_sha256": metadata.get("dataset_sha256"),
                    "validation_metrics": metadata.get("validation_metrics", {}),
                    "test_metrics": metadata.get("test_metrics", {}),
                    "quant_baseline_test_metrics": metadata.get("quant_baseline_test_metrics", {}),
                    "quant_backtest_test_metrics": metadata.get("quant_backtest_test_metrics", {}),
                    "ai_quant_backtest_test_metrics": metadata.get("ai_quant_backtest_test_metrics", {}),
                    "heldout_metrics": heldout_evaluation.get("metrics", {}),
                    "heldout_quant_baseline_metrics": heldout_evaluation.get("quant_baseline_metrics", {}),
                    "heldout_quant_backtest_metrics": heldout_evaluation.get("quant_backtest_metrics", {}),
                    "heldout_ai_quant_backtest_metrics": heldout_evaluation.get("ai_quant_backtest_metrics", {}),
                    "heldout_sessions": heldout_evaluation.get("sessions", []),
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
        holdout_dates = _validated_session_dates(holdout_sessions)
        if normalized_market not in _MARKETS or not normalized_symbol:
            raise ValueError("market, symbol, and independent holdout sessions are required")
        holdout_start = holdout_dates[0]
        eligible: list[tuple[float, str, int, int, int, str, str]] = []
        with self._governance_guard():
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
                            or metadata.get("training_config", {}).get("symbol") != normalized_symbol
                            or metadata.get("threshold_selection_method")
                            != VALIDATION_THRESHOLD_SELECTION_METHOD):
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
                    brier = metrics.get("quant_candidate_brier") if isinstance(metrics, dict) else None
                    sample_count = metrics.get("quant_candidate_sample_count") if isinstance(metrics, dict) else None
                    candidate_session_count = metrics.get("quant_candidate_session_count") if isinstance(metrics, dict) else None
                    positive_count = metrics.get("quant_candidate_positive_label_count") if isinstance(metrics, dict) else None
                    negative_count = metrics.get("quant_candidate_negative_label_count") if isinstance(metrics, dict) else None
                    if (isinstance(brier, bool) or not isinstance(brier, (int, float))
                            or not math.isfinite(float(brier)) or not 0 <= float(brier) <= 1):
                        continue
                    if (not _is_finite_integer(sample_count)
                            or sample_count < MIN_MODEL_VALIDATION_QUANT_CANDIDATE_SAMPLES
                            or not _is_finite_integer(candidate_session_count)
                            or candidate_session_count < MIN_MODEL_HOLDOUT_SELECTION_VALIDATION_SESSIONS
                            or not _is_finite_integer(positive_count)
                            or positive_count <= 0
                            or not _is_finite_integer(negative_count)
                            or negative_count <= 0
                            or positive_count + negative_count != sample_count
                            or candidate_session_count > len(validation_sessions)):
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
                    eligible.append((float(brier), model_id, len(validation_sessions), int(sample_count),
                                     int(candidate_session_count), latest_training_session, cohort_sha256))
                except (FileNotFoundError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    continue
        if not eligible:
            raise ValueError(
                "no unapproved candidate has three distinct prior validation sessions and sufficient "
                "ORB/VWAP validation samples with a valid candidate Brier score"
            )
        latest_training_session = max(value[5] for value in eligible)
        latest_candidates = [value for value in eligible if value[5] == latest_training_session]
        latest_cohorts = {value[6] for value in latest_candidates}
        if len(latest_cohorts) != 1:
            raise ValueError(
                "multiple candidate training cohorts share the latest test session; refusing incomparable selection"
            )
        comparable_candidates = [value for value in latest_candidates if value[6] in latest_cohorts]
        brier, model_id, validation_session_count, candidate_sample_count, candidate_session_count, _, _ = min(
            comparable_candidates, key=lambda value: (value[0], value[1])
        )
        return {
            "model_id": model_id,
            "selection_method": "lowest_validation_quant_candidate_brier",
            "validation_quant_candidate_brier": brier,
            "validation_session_count": validation_session_count,
            "validation_quant_candidate_sample_count": candidate_sample_count,
            "validation_quant_candidate_session_count": candidate_session_count,
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
        feature_config = _feature_quant_config(verified["training_config"])

        def entry_filter(bars: list[MinuteBar]) -> bool:
            features = latest_features(bars, market=normalized_market, quant_config=feature_config)
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
        heldout_evaluation = metadata.get("heldout_evaluation")
        heldout_evaluation = heldout_evaluation if isinstance(heldout_evaluation, dict) else {}
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
            "heldout_quant_backtest_metrics": heldout_evaluation.get("quant_backtest_metrics"),
            "heldout_ai_quant_backtest_metrics": heldout_evaluation.get("ai_quant_backtest_metrics"),
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
            "heldout_evaluation": evaluation,
            "artifact_sha256": metadata.get("artifact_sha256"),
            "threshold": metadata.get("threshold"),
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
        with self._governance_guard():
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
            if metadata.get("threshold_selection_method") != VALIDATION_THRESHOLD_SELECTION_METHOD:
                return False
            evaluation = metadata.get("heldout_evaluation")
            if not isinstance(evaluation, dict):
                return False
            if (evaluation.get("market") != metadata.get("market")
                    or evaluation.get("symbol") != metadata.get("training_config", {}).get("symbol")):
                return False
            selection = evaluation.get("candidate_selection")
            if (not isinstance(selection, dict)
                    or selection.get("model_id") != metadata.get("model_id")
                    or selection.get("selection_method") != "lowest_validation_quant_candidate_brier"
                    or isinstance(selection.get("validation_quant_candidate_brier"), bool)
                    or not isinstance(selection.get("validation_quant_candidate_brier"), (int, float))
                    or not math.isfinite(float(selection["validation_quant_candidate_brier"]))
                    or not 0 <= float(selection["validation_quant_candidate_brier"]) <= 1
                    or selection.get("validation_session_count", 0)
                    < MIN_MODEL_HOLDOUT_SELECTION_VALIDATION_SESSIONS
                    or selection.get("validation_quant_candidate_sample_count", 0)
                    < MIN_MODEL_VALIDATION_QUANT_CANDIDATE_SAMPLES
                    or selection.get("validation_quant_candidate_session_count", 0)
                    < MIN_MODEL_HOLDOUT_SELECTION_VALIDATION_SESSIONS
                    or selection.get("eligible_candidate_count", 0) < 1):
                return False
            validation_metrics = metadata.get("validation_metrics")
            if not isinstance(validation_metrics, dict):
                return False
            for selection_key, metric_key in (
                ("validation_quant_candidate_brier", "quant_candidate_brier"),
                ("validation_quant_candidate_sample_count", "quant_candidate_sample_count"),
                ("validation_quant_candidate_session_count", "quant_candidate_session_count"),
            ):
                selected_value = selection.get(selection_key)
                metric_value = validation_metrics.get(metric_key)
                if (isinstance(selected_value, bool) or not isinstance(selected_value, (int, float))
                        or not math.isfinite(float(selected_value))
                        or isinstance(metric_value, bool) or not isinstance(metric_value, (int, float))
                        or not math.isfinite(float(metric_value))
                        or float(selected_value) != float(metric_value)):
                    return False
                if metric_key != "quant_candidate_brier" and not _is_finite_integer(selected_value):
                    return False
            positive_count = validation_metrics.get("quant_candidate_positive_label_count")
            negative_count = validation_metrics.get("quant_candidate_negative_label_count")
            sample_count = validation_metrics.get("quant_candidate_sample_count")
            if (not _is_finite_integer(positive_count) or positive_count <= 0
                    or not _is_finite_integer(negative_count) or negative_count <= 0
                    or positive_count + negative_count != sample_count):
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
            raw_heldout_sessions = evaluation.get("sessions")
            if (not isinstance(raw_heldout_sessions, list) or not raw_heldout_sessions
                    or any(not isinstance(value, str) for value in raw_heldout_sessions)
                    or len(set(raw_heldout_sessions)) != len(raw_heldout_sessions)):
                return False
            heldout_sessions = {date.fromisoformat(value) for value in raw_heldout_sessions}
            if (len(heldout_sessions) < MIN_MODEL_APPROVAL_TEST_SESSIONS
                    or heldout_sessions & (train | validation | test)
                    or max(test) >= min(heldout_sessions)):
                return False
            heldout_metrics = evaluation.get("metrics")
            heldout_baseline_metrics = evaluation.get("quant_baseline_metrics")
            ai = evaluation.get("ai_quant_backtest_metrics")
            quant = evaluation.get("quant_backtest_metrics")
            if (not isinstance(heldout_metrics, dict) or not isinstance(heldout_baseline_metrics, dict)
                    or not isinstance(ai, dict) or not isinstance(quant, dict)):
                return False
            if any(not _metrics_cover_both_outcome_classes(metrics) for metrics in (
                    metadata.get("validation_metrics"), metadata.get("test_metrics"), heldout_metrics)):
                return False
            for metrics in (metadata.get("ai_quant_backtest_test_metrics"),
                            metadata.get("quant_backtest_test_metrics")):
                if not isinstance(metrics, dict):
                    return False
                session_count = Decimal(str(metrics["session_count"]))
                if not session_count.is_finite() or session_count != len(test):
                    return False
            config = metadata.get("training_config")
            if (not isinstance(config, dict) or not isinstance(config.get("symbol"), str)
                    or not config["symbol"].strip() or config.get("backtest_quantity_per_session") != 1):
                return False
            horizon = config.get("label_horizon_bars")
            if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
                return False
            opening_range_bars = config.get("backtest_opening_range_bars")
            if (isinstance(opening_range_bars, bool) or not isinstance(opening_range_bars, int)
                    or opening_range_bars <= 0):
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
                if not session_count.is_finite() or session_count != len(heldout_sessions):
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
        with self._governance_guard():
            metadata = self.manifest(model_id)
            if metadata.get("market") not in {"KR", "US"}:
                raise ValueError("market scope must be KR or US before model approval")
            if not self._supports_current_model_contract(metadata):
                raise ValueError("model feature or label schema is outdated and must be retrained")
            if not self._has_verified_data_provenance(metadata):
                raise ValueError("model lacks verified data-rights provenance and cannot be approved")
            if not metadata.get("test_sessions") or not metadata.get("test_metrics"):
                raise ValueError("model has no internal test evidence and cannot be approved")
            if metadata.get("threshold_selection_method") != VALIDATION_THRESHOLD_SELECTION_METHOD:
                raise ValueError("model lacks a profitable validation-selected AI threshold")
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
                raise ValueError("at least five distinct internal test sessions are required for model approval")
            heldout_evaluation = metadata.get("heldout_evaluation")
            if not isinstance(heldout_evaluation, dict):
                raise ValueError("independent holdout evidence is required for model approval")
            heldout_values = heldout_evaluation.get("sessions")
            if (not isinstance(heldout_values, list) or not heldout_values
                    or any(not isinstance(value, str) for value in heldout_values)
                    or len(set(heldout_values)) != len(heldout_values)):
                raise ValueError("independent holdout sessions must be distinct ISO dates")
            try:
                heldout_sessions = {date.fromisoformat(value) for value in heldout_values}
            except ValueError as exc:
                raise ValueError("independent holdout sessions must be distinct ISO dates") from exc
            if (len(heldout_sessions) < MIN_MODEL_APPROVAL_TEST_SESSIONS
                    or heldout_sessions & (train_sessions | validation_sessions | test_sessions)
                    or max(test_sessions) >= min(heldout_sessions)):
                raise ValueError("at least five distinct independent holdout sessions after internal test are required")
            ai_backtest = heldout_evaluation.get("ai_quant_backtest_metrics")
            quant_backtest = heldout_evaluation.get("quant_backtest_metrics")
            if not isinstance(ai_backtest, dict) or not isinstance(quant_backtest, dict):
                raise ValueError("held-out AI+quant and quant-only strategy backtests are required for approval")
            raw_trade_count = ai_backtest.get("trade_count")
            if isinstance(raw_trade_count, bool):
                raise ValueError("held-out strategy trade count must be a finite integer")
            try:
                if raw_trade_count is None:
                    raise KeyError("trade_count")
                ai_trade_count = Decimal(str(raw_trade_count))
                ai_net_pnl = Decimal(str(ai_backtest["net_pnl"]))
                quant_net_pnl = Decimal(str(quant_backtest["net_pnl"]))
            except (KeyError, ArithmeticError, TypeError, ValueError) as exc:
                raise ValueError("held-out strategy backtest metrics are incomplete") from exc
            if not ai_trade_count.is_finite() or ai_trade_count != ai_trade_count.to_integral_value():
                raise ValueError("held-out strategy trade count must be a finite integer")
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
                    "heldout_quant_backtest_metrics": quant_backtest,
                    "heldout_ai_quant_backtest_metrics": ai_backtest,
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
        with self._governance_guard():
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
        with self._governance_guard():
            try:
                events = self._read_governance_events()
                return events[-max(1, min(limit, 200)):]
            except (OSError, ValueError, json.JSONDecodeError):
                return []

    def _record_governance_event(self, event: dict[str, Any]) -> None:
        with self._governance_guard():
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
        validate_model_training_coverage(provenance, market=market, sessions=sessions)
        validate_model_session_integrity(market=market, symbol=normalized_symbol, sessions=sessions)
        # Derive every training row from the exact session mapping used for the
        # strategy backtest. Callers cannot pair samples from another dataset
        # with this dataset's reviewed provenance digest.
        samples = build_training_samples(
            sessions, market=market, horizon_bars=label_horizon_bars,
            round_trip_cost_bps=label_round_trip_cost_bps, quant_config=quant_config,
        )
        train, validation, test = _chronological_partitions(samples)
        if (len(train) < MIN_MODEL_TRAIN_SAMPLES
                or len(validation) < MIN_MODEL_VALIDATION_SAMPLES
                or len(test) < MIN_MODEL_TEST_SAMPLES):
            raise ValueError("insufficient samples per chronological partition")
        if len({sample.label for sample in train}) < 2:
            raise ValueError("training partition must contain both outcome classes")
        if len({sample.label for sample in validation}) < 2:
            raise ValueError("validation partition must contain both outcome classes")
        if len({sample.session for sample in samples}) >= MIN_MODEL_TRAINING_SESSIONS:
            candidate_reasons = _validation_quant_candidate_reasons(validation)
            if candidate_reasons:
                raise ValueError("validation ORB/VWAP candidates are insufficient: " + ", ".join(candidate_reasons))
        if len({sample.label for sample in test}) < 2:
            raise ValueError("test partition must contain both outcome classes")
        try:
            from lightgbm import LGBMClassifier
            from xgboost import XGBClassifier
            import joblib
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("Install the optional ai dependencies to train LightGBM and XGBoost models") from exc
        x_train = np.asarray([sample.features for sample in train], dtype=np.float32)
        y_train = np.asarray([sample.label for sample in train], dtype=np.int32)
        positive_train_count = int(np.count_nonzero(y_train == 1))
        negative_train_count = int(np.count_nonzero(y_train == 0))
        if positive_train_count <= 0 or negative_train_count <= 0:
            raise ValueError("training partition must contain both outcome classes")
        xgboost_scale_pos_weight = negative_train_count / positive_train_count
        candidates = {
            "lightgbm": LGBMClassifier(n_estimators=80, num_leaves=15, learning_rate=0.05,
                                        class_weight="balanced", verbosity=-1, random_state=42, n_jobs=1),
            "xgboost": XGBClassifier(n_estimators=80, max_depth=3, learning_rate=0.05, subsample=0.8,
                                     colsample_bytree=0.8, scale_pos_weight=xgboost_scale_pos_weight,
                                     eval_metric="logloss", random_state=42, n_jobs=1),
        }
        results: list[ModelTrainingResult] = []
        threshold_rejections: list[dict[str, Any]] = []
        created_paths: list[Path] = []
        train_dates = tuple(sorted({sample.session.isoformat() for sample in train}))
        validation_dates = tuple(sorted({sample.session.isoformat() for sample in validation}))
        test_dates = tuple(sorted({sample.session.isoformat() for sample in test}))
        test_session_dates = {sample.session for sample in test}
        test_bars = {session: bars for session, bars in sessions.items() if session in test_session_dates}
        if set(test_bars) != test_session_dates:
            raise ValueError("every held-out sample session must have source bars for strategy backtesting")
        approval_ready_session_count = len({sample.session for sample in samples}) >= MIN_MODEL_TRAINING_SESSIONS
        threshold_selection_method = (
            VALIDATION_THRESHOLD_SELECTION_METHOD
            if approval_ready_session_count else DIAGNOSTIC_THRESHOLD_SELECTION_METHOD
        )
        quant_backtest_test_metrics = _backtest_metrics([
            backtest_orb_vwap(market, normalized_symbol, test_bars[session], quant_config)
            for session in sorted(test_bars)
        ])
        training_config = {
            "symbol": normalized_symbol,
            "training_positive_samples": positive_train_count,
            "training_negative_samples": negative_train_count,
            "lightgbm_class_weight": "balanced",
            "xgboost_scale_pos_weight": xgboost_scale_pos_weight,
            "label_version": LABEL_VERSION,
            "backtest_version": BACKTEST_VERSION,
            "backtest_spread_model": BACKTEST_SPREAD_MODEL,
            "label_horizon_bars": label_horizon_bars,
            "label_round_trip_cost_bps": str(label_round_trip_cost_bps),
            "backtest_quantity_per_session": 1,
            "backtest_opening_range_bars": quant_config.opening_range_bars,
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
        try:
            for name, model in candidates.items():
                model.fit(x_train, y_train)
                try:
                    threshold = _select_validation_threshold(
                        model, validation,
                        allow_diagnostic_fallback=not approval_ready_session_count,
                    )
                except ValueError as exc:
                    if "no positive-net AI threshold with at least" not in str(exc):
                        raise
                    threshold_rejections.append({
                        "model_type": name,
                        "failure_code": "no_profitable_validation_ai_threshold",
                        **({"validation": exc.diagnostics}
                           if isinstance(exc, ValidationThresholdSelectionError) else {}),
                    })
                    continue
                validation_metrics = _metrics(model, validation, threshold=threshold)
                test_metrics = _metrics(model, test, threshold=threshold)
                quant_baseline_test_metrics = _quant_baseline_metrics(test)
                def ai_entry_filter(history: list[MinuteBar]) -> bool:
                    features = latest_features(history, market=market, quant_config=quant_config)
                    probability = _positive_probability(model, features)
                    return probability >= threshold

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
                    created_paths.append(artifact_path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                manifest = {
                    "model_id": model_id, "market": market, "model_type": name, "artifact": artifact_name,
                    "artifact_sha256": digest, "feature_names": list(FEATURE_NAMES), "feature_version": FEATURE_VERSION,
                    "threshold": threshold,
                    "threshold_selection_method": threshold_selection_method,
                    "approved": False,
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
                self._write_json_atomic(manifest_path, manifest)
                created_paths.append(manifest_path)
                results.append(ModelTrainingResult(model_id, market, name, threshold, validation_metrics, test_metrics,
                                                   quant_baseline_test_metrics, quant_backtest_test_metrics,
                                                   ai_quant_backtest_test_metrics, training_config,
                                                   train_dates, validation_dates,
                                                   test_dates, digest))
            if not results:
                raise NoProfitableValidationThresholdError(threshold_rejections)
            if threshold_rejections:
                diagnostics = {"threshold_selection_rejections": threshold_rejections}
                for index, result in enumerate(results):
                    manifest_path = self.root / f"{result.model_id}.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["training_diagnostics"] = diagnostics
                    self._write_json_atomic(manifest_path, manifest)
                    results[index] = replace(result, training_diagnostics=diagnostics)
        except Exception:
            for path in created_paths:
                path.unlink(missing_ok=True)
            raise
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
        with self._governance_guard():
            metadata = self.manifest(model_id)
            if metadata.get("approved") is True:
                raise ValueError("approved models cannot be rewritten with new evaluation evidence")
            if metadata.get("heldout_evaluation") is not None:
                raise ValueError("candidate already has independent holdout evidence and cannot be re-evaluated")
            holdout_dates = _validated_session_dates(sessions)
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
            if holdout_dates[0] <= prior_end:
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
                event_evaluation = event.get("heldout_evaluation")
                event_evaluation_sessions = (event_evaluation.get("sessions")
                                             if isinstance(event_evaluation, dict) else None)
                prior_dates = event.get("heldout_sessions", event.get("test_sessions", event_evaluation_sessions))
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
            validate_model_training_coverage(
                provenance, market=market, sessions=sessions,
                minimum_sessions=MIN_MODEL_APPROVAL_TEST_SESSIONS,
                require_spreads=True,
            )
            validate_model_session_integrity(
                market=market, symbol=symbol, sessions=sessions, require_spreads=True,
            )
            config_data = metadata["training_config"]
            quant_config = QuantConfig(
                opening_range_bars=int(config_data["backtest_opening_range_bars"]),
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
                features = latest_features(history, market=market, quant_config=quant_config)
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
            heldout_dates = tuple(sorted(value.isoformat() for value in actual_dates))
            updated = json.loads(json.dumps(metadata))
            updated["heldout_evaluation"] = {
                "market": market,
                "symbol": symbol,
                "candidate_selection": selection,
                "dataset_sha256": dataset_sha256,
                "data_provenance": provenance,
                "sessions": list(heldout_dates),
                "metrics": test_metrics,
                "quant_baseline_metrics": _quant_baseline_metrics(samples),
                "quant_backtest_metrics": quant_metrics,
                "ai_quant_backtest_metrics": ai_metrics,
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
                    "heldout_evaluation": updated["heldout_evaluation"],
                    "artifact_sha256": metadata["artifact_sha256"],
                    "threshold": metadata["threshold"],
                })
            except Exception:
                self._write_json_atomic(self.root / f"{model_id}.json", metadata)
                raise
            return {
                "model_id": model_id, "approved": False,
                "heldout_sessions": list(heldout_dates), "heldout_metrics": test_metrics,
                "heldout_quant_baseline_metrics": _quant_baseline_metrics(samples),
                "heldout_quant_backtest_metrics": quant_metrics,
                "heldout_ai_quant_backtest_metrics": ai_metrics,
                "approval_criteria_met": _meets_holdout_approval_criteria(ai_metrics, quant_metrics),
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
        features = latest_features(bars, market=market,
                                   quant_config=_feature_quant_config(metadata.get("training_config", {})))
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

