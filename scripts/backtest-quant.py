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
from app.backtest_metrics import session_net_return_bps, summarize_sessions, summarize_spread_coverage, summarize_trades
from app.market_scope import require_cli_operational_market
from app.quant import BacktestResult, QuantConfig, backtest_orb_vwap, risk_bounded_quantity


def _session_net_return_value(result: BacktestResult) -> str | None:
    return_bps = session_net_return_bps(result)
    return None if return_bps is None else str(return_bps)


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
    parser.add_argument("--sizing-equity", type=Decimal,
                        help="optional single-symbol starting equity for risk and concentration sizing")
    parser.add_argument("--max-position-notional", type=Decimal,
                        help="required with --sizing-equity; maximum notional per position")
    parser.add_argument("--trade-risk-equity-pct", type=Decimal, default=Decimal("0.25"),
                        help="risk budget per entry as percent of the supplied sizing equity (maximum 0.25)")
    parser.add_argument("--max-position-equity-pct", type=Decimal, default=Decimal("10"),
                        help="maximum single-symbol allocation as percent of supplied equity (maximum 10)")
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
    if args.sizing_equity is not None:
        required_sizing_values = (
            args.sizing_equity, args.max_position_notional,
            args.trade_risk_equity_pct, args.max_position_equity_pct,
        )
        if (any(value is None or not value.is_finite() for value in required_sizing_values)
                or args.sizing_equity <= 0 or args.max_position_notional <= 0):
            parser.error("--sizing-equity requires positive equity and --max-position-notional values")
        if not Decimal(0) < args.trade_risk_equity_pct <= Decimal("0.25"):
            parser.error("--trade-risk-equity-pct must be positive and no greater than the approved 0.25 percent")
        if not Decimal(0) < args.max_position_equity_pct <= Decimal("10"):
            parser.error("--max-position-equity-pct must be positive and no greater than the approved 10 percent")
    elif (args.max_position_notional is not None
            or args.trade_risk_equity_pct != Decimal("0.25")
            or args.max_position_equity_pct != Decimal("10")):
        parser.error("risk-sizing limits require --sizing-equity")

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
    quantity_sizer = None
    if args.sizing_equity is not None:
        def quantity_sizer(entry_price: Decimal, stop_distance: Decimal) -> int:
            return risk_bounded_quantity(
                entry_price=entry_price, stop_distance=stop_distance, equity=args.sizing_equity,
                max_order_notional=args.max_position_notional,
                trade_risk_equity_pct=args.trade_risk_equity_pct,
                max_position_equity_pct=args.max_position_equity_pct,
                buy_commission_bps=args.buy_commission_bps,
                sell_commission_bps=args.sell_commission_bps,
                sell_tax_bps=args.sell_tax_bps,
                slippage_bps=args.slippage_bps,
                maximum_spread_bps=args.maximum_spread_bps,
            )

    session_results = [
        (session, bars, backtest_orb_vwap(args.market, args.symbol, bars, config,
                                          quantity_sizer=quantity_sizer),
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
            "net_return_bps_on_traded_notional": _session_net_return_value(result),
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
            "session_performance": summarize_sessions(results),
        },
        "cost_assumptions_bps": {
            "buy_commission_per_fill": str(config.effective_buy_commission_bps),
            "sell_commission_per_fill": str(config.effective_sell_commission_bps),
            "sell_tax_per_fill": str(config.sell_tax_bps),
            "slippage_per_fill": str(config.slippage_bps),
        },
        "position_sizing": {
            "mode": "single_symbol_risk_bounded" if args.sizing_equity is not None else "fixed_one_share",
            "sizing_equity": str(args.sizing_equity) if args.sizing_equity is not None else None,
            "max_position_notional": (
                str(args.max_position_notional) if args.max_position_notional is not None else None
            ),
            "trade_risk_equity_pct": str(args.trade_risk_equity_pct) if args.sizing_equity is not None else None,
            "max_position_equity_pct": str(args.max_position_equity_pct) if args.sizing_equity is not None else None,
            "scope": "Each symbol is simulated independently; combined portfolio exposure and daily loss are not modeled.",
        },
        "executable": False,
        "limitations": [
            "Input data licensing, corrections, symbol mapping, and timestamp zone are the operator's responsibility.",
            "Fixed-one-share mode does not compound portfolio capital across sessions.",
            "Risk-based sizing is a single-symbol scenario; combined portfolio exposure, cash shared across symbols, and daily loss stops are not modeled.",
            "Risk sizing uses ATR stop distance and conservative estimated entry/exit costs; actual slippage and losses can be larger.",
            "Exchange fees and market impact must be reflected in the supplied assumptions where applicable.",
            "Entries require timestamped observed spreads within the configured limit; missing or untimestamped spread data is reported and is not treated as zero or a passing filter.",
        ],
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
