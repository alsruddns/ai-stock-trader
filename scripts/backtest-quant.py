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
from app.backtest_metrics import summarize_spread_coverage, summarize_trades
from app.market_scope import require_cli_operational_market
from app.quant import QuantConfig, backtest_orb_vwap


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic ORB/VWAP quant backtest over permission-cleared minute-bar CSV data."
    )
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--market", choices=("KR", "US"), required=True)
    parser.add_argument("--symbol", required=True, help="single instrument represented by the input CSV")
    parser.add_argument("--buy-commission-bps", type=Decimal, required=True,
                        help="verified buy commission in basis points")
    parser.add_argument("--sell-commission-bps", type=Decimal, required=True,
                        help="verified sell commission in basis points")
    parser.add_argument("--sell-tax-bps", type=Decimal, required=True,
                        help="verified sell tax/levy assumption in basis points; use zero only if verified")
    parser.add_argument("--slippage-bps", type=Decimal, required=True,
                        help="explicit slippage assumption per fill in basis points")
    parser.add_argument("--minimum-turnover", type=Decimal, default=Decimal("1000000"))
    parser.add_argument("--maximum-spread-bps", type=Decimal, default=Decimal("15"))
    args = parser.parse_args()
    try:
        require_cli_operational_market(args.market, "quant backtest")
    except ValueError as exc:
        parser.error(str(exc))
    costs = (args.buy_commission_bps, args.sell_commission_bps, args.sell_tax_bps, args.slippage_bps)
    if any(value < 0 for value in costs):
        parser.error("cost assumptions cannot be negative")
    if sum(costs) <= 0:
        parser.error("at least one explicit trading cost must be positive")

    try:
        provenance = load_data_provenance(args.csv_file, purpose="backtesting")
    except ValueError as exc:
        parser.error(str(exc))
    dataset_sha256 = hashlib.sha256(args.csv_file.read_bytes()).hexdigest()
    sessions = load_minute_bar_sessions(args.csv_file, market=args.market, expected_symbol=args.symbol)
    config = replace(QuantConfig(), commission_bps=Decimal("0"),
                     buy_commission_bps=args.buy_commission_bps,
                     sell_commission_bps=args.sell_commission_bps,
                     sell_tax_bps=args.sell_tax_bps, slippage_bps=args.slippage_bps,
                     minimum_turnover=args.minimum_turnover, maximum_spread_bps=args.maximum_spread_bps)
    session_results = [
        (session, bars, backtest_orb_vwap(args.market, args.symbol, bars, config),
         summarize_spread_coverage(bars))
        for session, bars in sorted(sessions.items())
    ]
    results = [result for _session, _bars, result, _coverage in session_results]
    trades = [trade for result in results for trade in result.trades]
    total_bars = sum(len(bars) for _session, bars, _result, _coverage in session_results)
    total_spread_bars = sum(coverage["spread_bars"] for _session, _bars, _result, coverage in session_results)
    total_timestamped_spread_bars = sum(
        coverage["spread_observation_time_bars"] for _session, _bars, _result, coverage in session_results
    )
    spread_coverage_percent = round(100 * total_spread_bars / total_bars, 2) if total_bars else None
    spread_observation_time_coverage_percent = (
        round(100 * total_timestamped_spread_bars / total_bars, 2) if total_bars else None
    )
    output = {
        "market": args.market,
        "symbol": args.symbol.strip().upper(),
        "dataset_sha256": dataset_sha256,
        "data_rights_verified": True,
        "data_provenance": provenance,
        "sessions": [{
            "date": result.session.isoformat(), "bars": result.data_points,
            **coverage,
            "signals": result.signals, "trades": len(result.trades),
            "gross_pnl": str(result.gross_pnl),
            "estimated_costs": str(result.costs), "net_pnl": str(result.net_pnl),
        } for _session, _bars, result, coverage in session_results],
        "summary": {
            "session_count": len(results), "bar_count": total_bars,
            "spread_bars": total_spread_bars,
            "spread_coverage_percent": spread_coverage_percent,
            "spread_observation_time_bars": total_timestamped_spread_bars,
            "spread_observation_time_coverage_percent": spread_observation_time_coverage_percent,
            "spread_filter_has_complete_input": total_bars > 0 and total_timestamped_spread_bars == total_bars,
            "signal_count": sum(result.signals for result in results), "trade_count": len(trades),
            "winning_trades": sum(trade.net_pnl > 0 for trade in trades),
            "gross_pnl": str(sum((result.gross_pnl for result in results), Decimal(0))),
            "estimated_costs": str(sum((result.costs for result in results), Decimal(0))),
            "net_pnl": str(sum((result.net_pnl for result in results), Decimal(0))),
            "trade_performance": summarize_trades(trades),
        },
        "cost_assumptions_bps": {
            "buy_commission_per_fill": str(config.effective_buy_commission_bps),
            "sell_commission_per_fill": str(config.effective_sell_commission_bps),
            "sell_tax_per_fill": str(config.sell_tax_bps),
            "slippage_per_fill": str(config.slippage_bps),
        },
        "quantity_per_session": 1,
        "executable": False,
        "limitations": [
            "Input data licensing, corrections, symbol mapping, and timestamp zone are the operator's responsibility.",
            "Each session is simulated independently at one share; results do not compound portfolio capital.",
            "Exchange fees and market impact must be reflected in the supplied assumptions where applicable.",
            "Entries require timestamped observed spreads within the configured limit; missing or untimestamped spread data is reported and is not treated as zero or a passing filter.",
        ],
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
