from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

from app.ai_pipeline import LocalModelRegistry
from app.data_import import load_minute_bar_sessions
from app.data_provenance import load_data_provenance


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate unapproved fixed model candidates on later, rights-reviewed holdout sessions."
    )
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--market", choices=("KR", "US"), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--registry", type=Path, default=Path(".local/models"))
    parser.add_argument("--model-id", action="append", dest="model_ids",
                        help="candidate to evaluate; may be supplied more than once (defaults to matching candidates)")
    args = parser.parse_args()
    try:
        provenance = load_data_provenance(args.csv_file, purpose="backtesting")
        digest = hashlib.sha256(args.csv_file.read_bytes()).hexdigest()
        sessions = load_minute_bar_sessions(args.csv_file, market=args.market, expected_symbol=args.symbol)
        registry = LocalModelRegistry(args.registry)
        candidates = [item for item in registry.list_models()
                      if item.get("market") == args.market and not item.get("approved")
                      and not item.get("integrity_error")
                      and registry.manifest(item["model_id"]).get("training_config", {}).get("symbol")
                      == args.symbol.strip().upper()]
        if args.model_ids:
            requested = set(args.model_ids)
            candidates = [item for item in candidates if item["model_id"] in requested]
            missing = requested - {item["model_id"] for item in candidates}
            if missing:
                raise ValueError("one or more requested models are missing, approved, or have a different market/symbol")
        if not candidates:
            raise ValueError("no matching unapproved candidate models were found")
        results = [registry.evaluate_candidate(
            item["model_id"], sessions, market=args.market, symbol=args.symbol,
            dataset_sha256=digest, dataset_provenance=provenance,
        ) for item in candidates]
        print(json.dumps({
            "market": args.market, "symbol": args.symbol.strip().upper(),
            "dataset_sha256": digest, "data_rights_verified": True,
            "candidate_count": len(results), "results": results,
            "automatic_approval": False, "automatic_activation": False,
            "orders_enabled": False,
        }, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
