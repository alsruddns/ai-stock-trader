from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

from app.data_import import load_minute_bar_sessions
from app.data_provenance import load_data_provenance
from app.quant import backtest_orb_vwap


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate one permission-cleared complete session and stage it for a local LEAN custom-data project."
    )
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--market", choices=("KR", "US"), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--data-folder", type=Path, default=repository / ".local" / "lean-data")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        provenance = load_data_provenance(args.csv_file, purpose="backtesting")
        sessions = load_minute_bar_sessions(args.csv_file, market=args.market, expected_symbol=args.symbol)
        if len(sessions) != 1:
            raise ValueError("LEAN adapter accepts exactly one exchange-local session per import")
        session, bars = next(iter(sessions.items()))
        result = backtest_orb_vwap(args.market, args.symbol, bars)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    digest = hashlib.sha256(args.csv_file.read_bytes()).hexdigest()
    destination = args.data_folder / "kis-minute-bars.csv"
    sidecar_destination = args.data_folder / "kis-minute-bars.csv.provenance.json"
    summary = {
        "market": args.market,
        "symbol": args.symbol.strip().upper(),
        "session": session.isoformat(),
        "bars": len(bars),
        "signals": result.signals,
        "trades": len(result.trades),
        "net_pnl": str(result.net_pnl),
        "dataset_sha256": digest,
        "rights_reviewed": provenance["rights_reviewed"],
        "staged": not args.dry_run,
        "destination": str(destination),
    }
    if not args.dry_run:
        args.data_folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.csv_file, destination)
        shutil.copyfile(args.csv_file.with_name(args.csv_file.name + ".provenance.json"), sidecar_destination)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
