from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

from app.data_import import load_minute_bar_sessions
from app.data_provenance import load_data_provenance
from app.market_scope import require_cli_operational_market
from app.portfolio_backtest import backtest_domestic_portfolio
from app.quant import QuantConfig, backtest_orb_vwap


def _dataset_argument(value: str) -> tuple[str, Path]:
    symbol, separator, path = value.partition("=")
    normalized = symbol.strip().upper()
    if not separator or not normalized or not path.strip():
        raise argparse.ArgumentTypeError("dataset must use SYMBOL=CSV_PATH")
    return normalized, Path(path.strip())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay domestic one-symbol candidate trades under shared portfolio capital and risk limits."
    )
    parser.add_argument("--dataset", type=_dataset_argument, action="append", required=True,
                        metavar="SYMBOL=CSV", help="repeat once per KR symbol; each CSV needs a reviewed rights sidecar")
    parser.add_argument("--starting-equity", type=Decimal, required=True,
                        help="hypothetical KRW capital; this does not query a broker account")
    parser.add_argument("--max-order-notional", type=Decimal, required=True,
                        help="hard simulated per-entry KRW notional ceiling")
    parser.add_argument("--buy-commission-bps", type=Decimal, required=True)
    parser.add_argument("--sell-commission-bps", type=Decimal, required=True)
    parser.add_argument("--sell-tax-bps", type=Decimal, required=True)
    parser.add_argument("--slippage-bps", type=Decimal, required=True,
                        help="estimated slippage for each fill")
    parser.add_argument("--minimum-turnover", type=Decimal, default=Decimal("1000000"))
    parser.add_argument("--maximum-spread-bps", type=Decimal, default=Decimal("15"))
    args = parser.parse_args()
    try:
        require_cli_operational_market("KR", "portfolio quant backtest")
    except ValueError as exc:
        parser.error(str(exc))

    symbols = [symbol for symbol, _path in args.dataset]
    if len(symbols) < 2:
        parser.error("portfolio backtest requires at least two distinct KR symbols")
    if len(set(symbols)) != len(symbols):
        parser.error("each KR symbol may be supplied only once")
    costs = (args.buy_commission_bps, args.sell_commission_bps, args.sell_tax_bps, args.slippage_bps)
    if any(not value.is_finite() or value < 0 for value in costs) or sum(costs) <= 0:
        parser.error("cost assumptions must be finite, non-negative, and at least one must be positive")
    if (not args.starting_equity.is_finite() or args.starting_equity <= 0
            or not args.max_order_notional.is_finite() or args.max_order_notional <= 0
            or not args.minimum_turnover.is_finite() or args.minimum_turnover < 0
            or not args.maximum_spread_bps.is_finite() or args.maximum_spread_bps < 0):
        parser.error("capital, order notional, and strategy thresholds are invalid")

    data: dict[str, dict[date, list]] = {}
    dataset_records: dict[str, dict[str, object]] = {}
    for symbol, csv_path in args.dataset:
        try:
            snapshot = csv_path.read_bytes()
            provenance = load_data_provenance(csv_path, purpose="backtesting", source_bytes=snapshot)
            sessions = load_minute_bar_sessions(csv_path, market="KR", expected_symbol=symbol,
                                                source_bytes=snapshot)
        except (OSError, ValueError) as exc:
            parser.error(f"{symbol} dataset rejected: {exc}")
        data[symbol] = sessions
        dataset_records[symbol] = {
            "dataset_sha256": hashlib.sha256(snapshot).hexdigest(),
            "data_rights_verified": True,
            "data_provenance": provenance,
            "session_count": len(sessions),
            "bar_count": sum(map(len, sessions.values())),
        }

    session_sets = {symbol: set(sessions) for symbol, sessions in data.items()}
    reference_sessions = next(iter(session_sets.values()))
    if not reference_sessions or any(sessions != reference_sessions for sessions in session_sets.values()):
        parser.error("all symbol datasets must contain the same non-empty set of KR session dates")

    config = replace(QuantConfig(), commission_bps=Decimal(0),
                     buy_commission_bps=args.buy_commission_bps,
                     sell_commission_bps=args.sell_commission_bps,
                     sell_tax_bps=args.sell_tax_bps, slippage_bps=args.slippage_bps,
                     minimum_turnover=args.minimum_turnover, maximum_spread_bps=args.maximum_spread_bps)
    results = []
    for session in sorted(reference_sessions):
        bars = {symbol: data[symbol][session] for symbol in sorted(data)}
        candidates = [backtest_orb_vwap("KR", symbol, bars[symbol], config) for symbol in sorted(bars)]
        portfolio = backtest_domestic_portfolio(
            candidates, bars, starting_equity=args.starting_equity,
            max_order_notional=args.max_order_notional, config=config,
        )
        results.append({
            "date": session.isoformat(),
            "input_bars_by_symbol": {symbol: len(bars[symbol]) for symbol in sorted(bars)},
            "candidate_signals": {result.symbol: result.signals for result in candidates},
            "candidate_trades": {result.symbol: len(result.trades) for result in candidates},
            "accepted_trades": [{
                "symbol": trade.symbol, "entry_time": trade.entry_time.isoformat(),
                "exit_time": trade.exit_time.isoformat(), "quantity": trade.quantity,
                "net_pnl": str(trade.net_pnl), "exit_reason": trade.exit_reason,
            } for trade in portfolio.trades],
            "rejected_entries": [
                {"symbol": symbol, "reason": reason} for symbol, reason in portfolio.rejected_entries
            ],
            "starting_equity": str(portfolio.starting_equity),
            "ending_equity": str(portfolio.ending_equity),
            "net_pnl": str(portfolio.net_pnl),
            "maximum_open_positions": portfolio.maximum_open_positions,
            "daily_loss_limit_hit": portfolio.daily_loss_limit_hit,
            "consecutive_loss_limit_hit": portfolio.consecutive_loss_limit_hit,
            "executable": portfolio.executable,
        })

    print(json.dumps({
        "market": "KR",
        "datasets": dataset_records,
        "sessions": results,
        "summary": {
            "session_count": len(results),
            "starting_equity_per_session": str(args.starting_equity),
            "net_pnl_across_independent_sessions": str(sum((Decimal(row["net_pnl"]) for row in results), Decimal(0))),
            "accepted_trade_count": sum(len(row["accepted_trades"]) for row in results),
            "executable": False,
        },
        "cost_assumptions_bps_per_fill": {
            "buy_commission": str(args.buy_commission_bps),
            "sell_commission": str(args.sell_commission_bps),
            "sell_tax": str(args.sell_tax_bps),
            "slippage": str(args.slippage_bps),
        },
        "risk_limits": {
            "trade_risk_equity_pct": "0.25",
            "max_position_equity_pct": "10",
            "max_open_positions": 3,
            "max_daily_loss_pct": "1",
            "max_consecutive_losses": 3,
            "leverage": "disabled",
        },
        "simulation_scope": (
            "Independent symbol candidate signals are combined under shared capital; candidate signal generation "
            "does not condition on other open positions. Each date is an independent, non-compounded session."
        ),
        "executable": False,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
