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
from app.data_provenance import load_data_provenance


def main() -> int:
    parser = argparse.ArgumentParser(description="Train read-only LightGBM/XGBoost candidates from a licensed minute-bar CSV.")
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--market", choices=("KR", "US"), required=True,
                        help="market scope; train and approve KR and US models separately")
    parser.add_argument("--registry", type=Path, default=Path(".local/models"))
    parser.add_argument("--horizon-bars", type=int, default=5)
    parser.add_argument("--round-trip-cost-bps", type=Decimal, default=Decimal("10"))
    args = parser.parse_args()
    try:
        provenance = load_data_provenance(args.csv_file, purpose="model_training")
    except ValueError as exc:
        parser.error(str(exc))
    dataset_sha256 = hashlib.sha256(args.csv_file.read_bytes()).hexdigest()
    sessions = load_minute_bar_sessions(args.csv_file)
    samples = build_training_samples(
        sessions, horizon_bars=args.horizon_bars, round_trip_cost_bps=args.round_trip_cost_bps,
    )
    results = LocalModelRegistry(args.registry).train_compare(
        samples, market=args.market, dataset_sha256=dataset_sha256, dataset_provenance=provenance,
    )
    print(json.dumps({"dataset_sha256": dataset_sha256,
                      "data_rights_verified": True,
                      "models": [result.__dict__ for result in results]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
