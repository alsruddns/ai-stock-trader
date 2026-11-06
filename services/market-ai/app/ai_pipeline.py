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
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .data_provenance import validate_data_provenance
from .quant import (MinuteBar, QuantConfig, _atr, _ema, _vwap,
                    backtest_orb_vwap, exchange_session_date, orb_vwap_entry_candidate)

FEATURE_NAMES = (
    "return_1m", "return_3m", "return_5m", "close_vwap_pct", "ema9_gap_pct",
    "ema20_gap_pct", "atr14_pct", "relative_volume20", "spread_bps", "session_progress",
)
FEATURE_VERSION = 2
MIN_MODEL_APPROVAL_TEST_SESSIONS = 5
MIN_MODEL_APPROVAL_TEST_TRADES = 5
_REGISTRY_LOCKS_GUARD = threading.Lock()
_REGISTRY_LOCKS: dict[Path, threading.RLock] = {}


_MARKET_SESSIONS = {
    "KR": (ZoneInfo("Asia/Seoul"), time(9, 0)),
    "US": (ZoneInfo("America/New_York"), time(9, 30)),
}


def _session_progress(timestamp: datetime, market: str) -> Decimal:
    """Fraction of the local regular session elapsed, clamped to [0, 1]."""
    normalized_market = market.strip().upper()
    if normalized_market not in _MARKET_SESSIONS:
        raise ValueError("market must be KR or US")
    zone, open_time = _MARKET_SESSIONS[normalized_market]
    local_time = timestamp.astimezone(zone).time().replace(tzinfo=None)
    local_seconds = local_time.hour * 3600 + local_time.minute * 60 + local_time.second
    open_seconds = open_time.hour * 3600 + open_time.minute * 60 + open_time.second
    seconds = local_seconds - open_seconds
    duration_seconds = 6 * 3600 + 30 * 60
    return min(Decimal(1), max(Decimal(0), Decimal(seconds) / Decimal(duration_seconds)))


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
    """Create samples using features available at t and a label from t+horizon.

    Sessions are processed independently, so labels never cross a close. The label
    is positive only when forward close-to-close return exceeds estimated round-trip
    costs. Caller owns data licensing and corporate-action/symbol normalization.
    """
    if horizon_bars <= 0 or warmup_bars <= 0 or round_trip_cost_bps < 0:
        raise ValueError("horizon, warmup, and transaction cost settings are invalid")
    normalized_market = market.strip().upper()
    if normalized_market not in _MARKET_SESSIONS:
        raise ValueError("market must be KR or US")
    samples: list[TrainingSample] = []
    required = max(warmup_bars, 21, 15, 10)
    for session, bars in sorted(sessions.items()):
        if not bars:
            continue
        if any(exchange_session_date(item.timestamp, normalized_market) != session for item in bars):
            raise ValueError("bar date does not match its session key")
        if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:])):
            raise ValueError("session bars must be strictly increasing")
        for index in range(required - 1, len(bars) - horizon_bars):
            current = bars[index]
            history = bars[: index + 1]
            prior = bars[max(0, index - 20):index]
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
            future_return = bars[index + horizon_bars].close / close - Decimal(1)
            forward_net_return_bps = future_return * Decimal(10_000) - round_trip_cost_bps
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
    if normalized_market not in _MARKET_SESSIONS:
        raise ValueError("market must be KR or US")
    if any(exchange_session_date(item.timestamp, normalized_market)
           != exchange_session_date(bars[0].timestamp, normalized_market) for item in bars):
        raise ValueError("inference input must contain one session")
    if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:])):
        raise ValueError("inference bars must be strictly increasing")
    current = bars[-1]
    if current.spread_bps is None:
        raise ValueError("latest bar must include spread")
    index = len(bars) - 1
    prior = bars[max(0, index - 20):index]
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
        _session_progress(current.timestamp, normalized_market),
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
    def _audit_path(self) -> Path:
        return self.root / "governance-audit.jsonl"

    def list_models(self) -> list[dict[str, Any]]:
        active_model = None
        if self._active_path.is_file():
            try:
                active_model = json.loads(self._active_path.read_text(encoding="utf-8")).get("model_id")
            except (OSError, json.JSONDecodeError):
                active_model = None
        items: list[dict[str, Any]] = []
        for manifest_path in sorted(self.root.glob("*.json"), key=lambda path: path.name):
            if manifest_path == self._active_path:
                continue
            try:
                metadata = self.manifest(manifest_path.stem)
                data_rights_verified = self._has_verified_data_provenance(metadata)
                feature_compatible = self._supports_current_features(metadata)
                approval_evidence_verified = self._has_valid_approval_evidence(metadata)
                approved = bool(metadata.get("approved", False) and data_rights_verified
                                and feature_compatible and approval_evidence_verified)
                items.append({
                    "model_id": metadata["model_id"], "market": metadata.get("market"), "model_type": metadata["model_type"],
                    "feature_version": metadata.get("feature_version"),
                    "feature_compatible": feature_compatible,
                    "data_rights_verified": data_rights_verified,
                    "approval_evidence_verified": approval_evidence_verified,
                    "approved": approved,
                    "active": metadata["model_id"] == active_model and approved and feature_compatible,
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

    def active_approved_model(self, market: str) -> dict[str, Any] | None:
        """Return the integrity-checked active model only when approved for this market."""
        requested_market = market.strip().upper()
        if requested_market not in {"KR", "US"} or not self._active_path.is_file():
            return None
        try:
            active_payload = json.loads(self._active_path.read_text(encoding="utf-8"))
            if not isinstance(active_payload, dict):
                return None
            active_id = active_payload.get("model_id")
            if not isinstance(active_id, str):
                return None
            metadata = self.manifest(active_id)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None
        if (metadata.get("market") != requested_market or not metadata.get("approved")
                or not self._supports_current_features(metadata)
                or not self._has_verified_data_provenance(metadata)
                or not self._has_valid_approval_evidence(metadata)):
            return None
        return metadata

    @staticmethod
    def _supports_current_features(metadata: dict[str, Any]) -> bool:
        return metadata.get("feature_version") == FEATURE_VERSION and metadata.get("feature_names") == list(FEATURE_NAMES)

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
            label_cost = Decimal(str(config["label_round_trip_cost_bps"]))
            commission = Decimal(str(config["backtest_commission_bps"]))
            slippage = Decimal(str(config["backtest_slippage_bps"]))
            if any(not value.is_finite() or value < 0 for value in (label_cost, commission, slippage)):
                return False
            # Labels span one entry and one exit. Keep their cost floor at least as
            # conservative as the backtest's per-side commission and slippage.
            if commission + slippage <= 0 or label_cost < 2 * (commission + slippage):
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
            if not self._supports_current_features(metadata):
                raise ValueError("model feature schema is outdated and must be retrained")
            if not self._has_verified_data_provenance(metadata):
                raise ValueError("model lacks verified data-rights provenance and cannot be approved")
            if not metadata.get("test_sessions") or not metadata.get("test_metrics"):
                raise ValueError("model has no held-out test evidence and cannot be approved")
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
            previous_metadata = json.loads(json.dumps(metadata))
            metadata["approved"] = True
            metadata["approval"] = {"approved_by": reviewer, "note": justification, "approved_at": datetime.now().astimezone().isoformat()}
            self._write_json_atomic(self.root / f"{model_id}.json", metadata)
            try:
                self._record_governance_event({
                    "action": "approve", "model_id": model_id, **metadata["approval"],
                    "dataset_sha256": metadata.get("dataset_sha256"),
                    "artifact_sha256": metadata["artifact_sha256"],
                    "train_sessions": metadata["train_sessions"],
                    "validation_sessions": metadata["validation_sessions"],
                    "test_sessions": metadata["test_sessions"],
                    "training_config": metadata.get("training_config", {}),
                    "quant_backtest_test_metrics": quant_backtest,
                    "ai_quant_backtest_test_metrics": ai_backtest,
                })
            except OSError:
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
            if not self._supports_current_features(metadata):
                raise ValueError("model feature schema is outdated and cannot be activated")
            if not self._has_verified_data_provenance(metadata):
                raise ValueError("model lacks verified data-rights provenance and cannot be activated")
            if not metadata.get("approved"):
                raise ValueError("only an approved model can become the advisory active model")
            if not self._has_valid_approval_evidence(metadata):
                raise ValueError("model lacks valid held-out comparative strategy evidence")
            previous_state = None
            if self._active_path.is_file():
                try:
                    previous_state = json.loads(self._active_path.read_text(encoding="utf-8"))
                    previous = previous_state.get("model_id")
                    if not isinstance(previous, str):
                        raise ValueError("active model ID is missing")
                    self.manifest(previous)
                except (OSError, json.JSONDecodeError, AttributeError, ValueError, FileNotFoundError) as exc:
                    raise ValueError("active model pointer is invalid and must be repaired before changing it") from exc
            else:
                previous = None
            changed_at = datetime.now().astimezone().isoformat()
            self._write_json_atomic(self._active_path, {"model_id": model_id, "changed_at": changed_at})
            event = {"action": action, "model_id": model_id, "previous_model_id": previous,
                     "changed_by": operator, "note": justification, "changed_at": changed_at}
            try:
                self._record_governance_event(event)
            except OSError:
                if previous_state is None:
                    self._active_path.unlink(missing_ok=True)
                else:
                    self._write_json_atomic(self._active_path, previous_state)
                raise
            return event

    def governance_events(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self._audit_path.is_file():
            return []
        with self._governance_lock:
            try:
                lines = self._audit_path.read_text(encoding="utf-8").splitlines()[-max(1, min(limit, 200)):]
                return [json.loads(line) for line in lines]
            except (OSError, json.JSONDecodeError):
                return []

    def _record_governance_event(self, event: dict[str, Any]) -> None:
        with self._audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

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

    def train_compare(self, samples: list[TrainingSample], *, market: str, symbol: str,
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
        minimum_label_cost = 2 * (quant_config.commission_bps + quant_config.slippage_bps)
        if label_round_trip_cost_bps < minimum_label_cost:
            raise ValueError("label round-trip cost must cover modeled entry and exit commission and slippage")
        if not re.fullmatch(r"[a-f0-9]{64}", dataset_sha256):
            raise ValueError("dataset_sha256 must be a lowercase SHA-256 digest")
        provenance = validate_data_provenance(dataset_provenance, expected_sha256=dataset_sha256,
                                              purpose="model_training")
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
            "label_horizon_bars": label_horizon_bars,
            "label_round_trip_cost_bps": str(label_round_trip_cost_bps),
            "backtest_quantity_per_session": 1,
            "backtest_commission_bps": str(quant_config.commission_bps),
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
                probability = float(model.predict_proba(np.asarray([features], dtype=np.float32))[0][1])
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

    def predict_preview(self, model_id: str, bars: list[MinuteBar], *, market: str) -> dict[str, Any]:
        try:
            import joblib
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("Install the optional ai dependencies to run inference") from exc
        metadata = self.manifest(model_id)
        if market.upper() != metadata.get("market"):
            raise ValueError("model market scope does not match the requested market")
        if not self._supports_current_features(metadata):
            raise ValueError("model feature schema is outdated and must be retrained")
        artifact = self.root / metadata["artifact"]
        model = joblib.load(artifact)
        features = latest_features(bars, market=market)
        probability = float(model.predict_proba(np.asarray([features], dtype=np.float32))[0][1])
        threshold = float(metadata["threshold"])
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

