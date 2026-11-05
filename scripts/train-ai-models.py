from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

from app.ai_pipeline import LocalModelRegistry, build_training_samples
from app.quant import MinuteBar


def main() -> int:
    parser = argparse.ArgumentParser(description="Train read-only LightGBM/XGBoost candidates from a licensed minute-bar CSV.")
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--registry", type=Path, default=Path(".local/models"))
    parser.add_argument("--horizon-bars", type=int, default=5)
    parser.add_argument("--round-trip-cost-bps", type=Decimal, default=Decimal("10"))
    args = parser.parse_args()
    sessions: dict[date, list[MinuteBar]] = defaultdict(list)
    with args.csv_file.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            timestamp = datetime.fromisoformat(row["timestamp"])
            sessions[timestamp.date()].append(MinuteBar(
                timestamp=timestamp,
                open=Decimal(row["open"]), high=Decimal(row["high"]), low=Decimal(row["low"]),
                close=Decimal(row["close"]), volume=int(row["volume"]),
                spread_bps=Decimal(row["spread_bps"]) if row.get("spread_bps") else None,
            ))
    samples = build_training_samples(
        sessions, horizon_bars=args.horizon_bars, round_trip_cost_bps=args.round_trip_cost_bps,
    )
    results = LocalModelRegistry(args.registry).train_compare(samples)
    print(json.dumps([result.__dict__ for result in results], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
