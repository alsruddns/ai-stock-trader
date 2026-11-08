from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import timezone
from decimal import Decimal
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

from app.data_import import load_minute_bar_sessions
from app.data_provenance import load_data_provenance
from app.ai_pipeline import LocalModelRegistry, predict_probability_series
from app.quant import QuantConfig, backtest_orb_vwap


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate one permission-cleared complete session and stage it for a local LEAN custom-data project."
    )
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--market", choices=("KR", "US"), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--data-folder", type=Path, default=repository / ".local" / "lean-data")
    parser.add_argument("--model-id", help="fixed LightGBM/XGBoost candidate ID for AI+quant parity replay")
    parser.add_argument("--registry", type=Path, default=repository / ".local" / "models")
    parser.add_argument("--buy-commission-bps", type=Decimal, default=Decimal("5"))
    parser.add_argument("--sell-commission-bps", type=Decimal, default=Decimal("5"))
    parser.add_argument("--sell-tax-bps", type=Decimal, default=Decimal("0"))
    parser.add_argument("--slippage-bps", type=Decimal, default=Decimal("5"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cost_values = (args.buy_commission_bps, args.sell_commission_bps,
                   args.sell_tax_bps, args.slippage_bps)
    if any(not value.is_finite() or value < 0 or value > Decimal("10000") for value in cost_values):
        parser.error("cost assumptions must be finite basis-point values from zero through 10000")
    quant_config = QuantConfig(
        commission_bps=Decimal("0"),
        buy_commission_bps=args.buy_commission_bps,
        sell_commission_bps=args.sell_commission_bps,
        sell_tax_bps=args.sell_tax_bps,
        slippage_bps=args.slippage_bps,
    )

    try:
        provenance = load_data_provenance(args.csv_file, purpose="backtesting")
        sessions = load_minute_bar_sessions(args.csv_file, market=args.market, expected_symbol=args.symbol)
        if len(sessions) != 1:
            raise ValueError("LEAN adapter accepts exactly one exchange-local session per import")
        session, bars = next(iter(sessions.items()))
        quant_result = backtest_orb_vwap(args.market, args.symbol, bars, quant_config)
        model_metadata = None
        score_by_timestamp = None
        ai_threshold = None
        ai_result = quant_result
        if args.model_id:
            registry = LocalModelRegistry(args.registry)
            model_metadata = registry.manifest(args.model_id)
            training_config = model_metadata.get("training_config", {})
            if (model_metadata.get("market") != args.market
                    or training_config.get("symbol") != args.symbol.strip().upper()):
                raise ValueError("model market and symbol must match the LEAN replay data")
            if not registry._supports_current_features(model_metadata):
                raise ValueError("model feature schema is outdated and must be retrained")
            if not registry._has_verified_data_provenance(model_metadata):
                raise ValueError("model lacks verified data-rights provenance")
            import joblib

            model = joblib.load(registry.root / model_metadata["artifact"])
            score_by_timestamp = predict_probability_series(model, bars, market=args.market)
            ai_threshold = float(model_metadata["threshold"])

            def ai_entry_filter(history):
                probability = score_by_timestamp.get(history[-1].timestamp.isoformat())
                if probability is None:
                    raise ValueError("AI score is missing for a quant strategy candidate bar")
                return probability >= ai_threshold

            ai_result = backtest_orb_vwap(
                args.market, args.symbol, bars, quant_config, entry_filter=ai_entry_filter
            )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    digest = hashlib.sha256(args.csv_file.read_bytes()).hexdigest()
    destination = args.data_folder / "kis-minute-bars.csv"
    sidecar_destination = args.data_folder / "kis-minute-bars.csv.provenance.json"
    score_bytes = None
    score_digest = None
    score_count = None
    score_destination = args.data_folder / "kis-ai-scores.csv"
    score_sidecar_destination = args.data_folder / "kis-ai-scores.csv.provenance.json"
    if score_by_timestamp is not None:
        lines = ["timestamp_utc,probability"]
        for bar in bars:
            probability = score_by_timestamp.get(bar.timestamp.isoformat())
            if probability is not None:
                timestamp_utc = bar.timestamp.astimezone(timezone.utc).isoformat()
                lines.append(f"{timestamp_utc},{probability:.17g}")
        score_bytes = ("\n".join(lines) + "\n").encode("utf-8")
        score_digest = hashlib.sha256(score_bytes).hexdigest()
        score_count = len(lines) - 1
    summary = {
        "market": args.market,
        "symbol": args.symbol.strip().upper(),
        "session": session.isoformat(),
        "bars": len(bars),
        "signals": ai_result.signals,
        "quant_signals": quant_result.signals,
        "trades": len(ai_result.trades),
        "net_pnl": str(ai_result.net_pnl),
        "dataset_sha256": digest,
        "rights_reviewed": provenance["rights_reviewed"],
        "cost_assumptions_bps": {
            "buy_commission": str(quant_config.effective_buy_commission_bps),
            "sell_commission": str(quant_config.effective_sell_commission_bps),
            "sell_tax": str(quant_config.sell_tax_bps),
            "slippage_per_fill": str(quant_config.slippage_bps),
        },
        "staged": not args.dry_run,
        "destination": str(destination),
    }
    if model_metadata is not None:
        summary.update({
            "model_id": model_metadata["model_id"],
            "model_artifact_sha256": model_metadata["artifact_sha256"],
            "model_threshold": ai_threshold,
            "quant_signals": quant_result.signals,
            "quant_trades": len(quant_result.trades),
            "quant_net_pnl": str(quant_result.net_pnl),
            "ai_score_sha256": score_digest,
            "ai_score_count": score_count,
            "ai_score_destination": str(score_destination),
            "executable": False,
        })
    if not args.dry_run:
        args.data_folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.csv_file, destination)
        shutil.copyfile(args.csv_file.with_name(args.csv_file.name + ".provenance.json"), sidecar_destination)
        if model_metadata is not None and score_bytes is not None:
            temporary = score_destination.with_suffix(score_destination.suffix + ".tmp")
            try:
                with temporary.open("wb") as stream:
                    stream.write(score_bytes)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, score_destination)
            finally:
                temporary.unlink(missing_ok=True)
            score_sidecar = {
                "schema_version": 1,
                "model_id": model_metadata["model_id"],
                "model_artifact_sha256": model_metadata["artifact_sha256"],
                "market": args.market,
                "symbol": args.symbol.strip().upper(),
                "session": session.isoformat(),
                "dataset_sha256": digest,
                "dataset_rights_verified": True,
                "score_sha256": score_digest,
                "score_count": score_count,
                "threshold": ai_threshold,
                "executable": False,
            }
            temporary_sidecar = score_sidecar_destination.with_suffix(
                score_sidecar_destination.suffix + ".tmp"
            )
            try:
                with temporary_sidecar.open("w", encoding="utf-8", newline="\n") as stream:
                    json.dump(score_sidecar, stream, ensure_ascii=False, sort_keys=True, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_sidecar, score_sidecar_destination)
            finally:
                temporary_sidecar.unlink(missing_ok=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
