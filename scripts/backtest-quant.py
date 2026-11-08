from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

from app.data_import import load_minute_bar_sessions
from app.data_provenance import load_data_provenance
from app.quant import QuantConfig, backtest_orb_vwap


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic ORB/VWAP quant backtest over permission-cleared minute-bar CSV data."
    )
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--market", choices=("KR", "US"), required=True)
    parser.add_argument("--symbol", required=True, help="single instrument represented by the input CSV")
    parser.add_argument("--commission-bps", type=Decimal, required=True,
                        help="verified commission assumption per fill in basis points")
    parser.add_argument("--slippage-bps", type=Decimal, required=True,
                        help="explicit slippage assumption per fill in basis points")
    parser.add_argument("--minimum-turnover", type=Decimal, default=Decimal("1000000"))
    parser.add_argument("--maximum-spread-bps", type=Decimal, default=Decimal("15"))
    args = parser.parse_args()

    try:
        provenance = load_data_provenance(args.csv_file, purpose="backtesting")
    except ValueError as exc:
        parser.error(str(exc))
    dataset_sha256 = hashlib.sha256(args.csv_file.read_bytes()).hexdigest()
    sessions = load_minute_bar_sessions(args.csv_file, market=args.market, expected_symbol=args.symbol)
    config = replace(QuantConfig(), commission_bps=args.commission_bps, slippage_bps=args.slippage_bps,
                     minimum_turnover=args.minimum_turnover, maximum_spread_bps=args.maximum_spread_bps)
    results = [backtest_orb_vwap(args.market, args.symbol, bars, config)
               for _, bars in sorted(sessions.items())]
    trades = [trade for result in results for trade in result.trades]
    output = {
        "market": args.market,
        "symbol": args.symbol.strip().upper(),
        "dataset_sha256": dataset_sha256,
        "data_rights_verified": True,
        "data_provenance": provenance,
        "sessions": [{
            "date": result.session.isoformat(), "bars": result.data_points, "signals": result.signals,
            "trades": len(result.trades), "gross_pnl": str(result.gross_pnl),
            "estimated_costs": str(result.costs), "net_pnl": str(result.net_pnl),
        } for result in results],
        "summary": {
            "session_count": len(results), "bar_count": sum(result.data_points for result in results),
            "signal_count": sum(result.signals for result in results), "trade_count": len(trades),
            "winning_trades": sum(trade.net_pnl > 0 for trade in trades),
            "gross_pnl": str(sum((result.gross_pnl for result in results), Decimal(0))),
            "estimated_costs": str(sum((result.costs for result in results), Decimal(0))),
            "net_pnl": str(sum((result.net_pnl for result in results), Decimal(0))),
        },
        "cost_assumptions_bps": {"commission_per_fill": str(config.commission_bps),
                                 "slippage_per_fill": str(config.slippage_bps)},
        "quantity_per_session": 1,
        "executable": False,
        "limitations": [
            "Input data licensing, corrections, symbol mapping, and timestamp zone are the operator's responsibility.",
            "Each session is simulated independently at one share; results do not compound portfolio capital.",
            "Estimated costs do not include account-specific taxes, exchange fees, or market impact.",
        ],
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
