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
from app.market_scope import require_cli_operational_market


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select one unapproved candidate by validation Brier and evaluate it on later holdout sessions."
    )
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--market", choices=("KR", "US"), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--registry", type=Path, default=Path(".local/models"))
    args = parser.parse_args()
    try:
        require_cli_operational_market(args.market, "AI candidate holdout evaluation")
    except ValueError as exc:
        parser.error(str(exc))
    try:
        provenance = load_data_provenance(args.csv_file, purpose="backtesting")
        digest = hashlib.sha256(args.csv_file.read_bytes()).hexdigest()
        sessions = load_minute_bar_sessions(args.csv_file, market=args.market, expected_symbol=args.symbol)
        registry = LocalModelRegistry(args.registry)
        selection = registry.select_candidate_for_holdout(args.market, args.symbol, sessions)
        result = registry.evaluate_candidate(
            selection["model_id"], sessions, market=args.market, symbol=args.symbol,
            dataset_sha256=digest, dataset_provenance=provenance,
        )
        print(json.dumps({
            "market": args.market, "symbol": args.symbol.strip().upper(),
            "dataset_sha256": digest, "data_rights_verified": True,
            "candidate_count": 1, "selection": selection, "results": [result],
            "automatic_approval": False, "automatic_activation": False,
            "orders_enabled": False,
        }, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
