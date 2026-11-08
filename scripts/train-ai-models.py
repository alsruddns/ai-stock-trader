from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

from app.ai_pipeline import LocalModelRegistry, build_training_samples
from app.data_import import load_minute_bar_sessions
from app.data_provenance import load_data_provenance, validate_model_training_coverage
from app.quant import QuantConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Train read-only LightGBM/XGBoost candidates from a licensed minute-bar CSV.")
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--market", choices=("KR", "US"), required=True,
                        help="market scope; train and approve KR and US models separately")
    parser.add_argument("--symbol", required=True,
                        help="single instrument used for held-out quant and AI+quant strategy backtests")
    parser.add_argument("--registry", type=Path, default=Path(".local/models"))
    parser.add_argument("--horizon-bars", type=int, default=5)
    parser.add_argument("--round-trip-cost-bps", type=Decimal, default=Decimal("20"),
                        help="cost deducted from forward labels; default matches two 5 bps commission and slippage fills")
    parser.add_argument("--commission-bps", type=Decimal, default=Decimal("5"))
    parser.add_argument("--slippage-bps", type=Decimal, default=Decimal("5"))
    args = parser.parse_args()
    try:
        provenance = load_data_provenance(args.csv_file, purpose="model_training")
    except ValueError as exc:
        parser.error(str(exc))
    dataset_sha256 = hashlib.sha256(args.csv_file.read_bytes()).hexdigest()
    sessions = load_minute_bar_sessions(args.csv_file, market=args.market, expected_symbol=args.symbol)
    try:
        validate_model_training_coverage(provenance, market=args.market, sessions=sessions)
    except ValueError as exc:
        parser.error(str(exc))
    quant_config = QuantConfig(commission_bps=args.commission_bps, slippage_bps=args.slippage_bps)
    samples = build_training_samples(
        sessions, market=args.market, horizon_bars=args.horizon_bars,
        round_trip_cost_bps=args.round_trip_cost_bps, quant_config=quant_config,
    )
    results = LocalModelRegistry(args.registry).train_compare(
        samples, market=args.market, symbol=args.symbol, sessions=sessions, quant_config=quant_config,
        label_horizon_bars=args.horizon_bars, label_round_trip_cost_bps=args.round_trip_cost_bps,
        dataset_sha256=dataset_sha256, dataset_provenance=provenance,
    )
    print(json.dumps({"dataset_sha256": dataset_sha256,
                      "data_rights_verified": True,
                      "models": [result.__dict__ for result in results]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
