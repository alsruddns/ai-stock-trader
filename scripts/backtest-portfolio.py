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
from app.ai_pipeline import LocalModelRegistry
from app.backtest_metrics import summarize_net_trade_pnls
from app.market_scope import require_cli_operational_market
from app.portfolio_backtest import (
    aggregate_compounded_drawdown, analyze_slippage_sensitivity, backtest_domestic_portfolio,
)
from app.quant import QuantConfig


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
    parser.add_argument("--slippage-scenario-bps", type=Decimal, action="append", default=None,
                        help="optional per-fill slippage stress case; repeat to compare scenarios")
    parser.add_argument("--minimum-turnover", type=Decimal, default=Decimal("1000000"))
    parser.add_argument("--maximum-spread-bps", type=Decimal, default=Decimal("15"))
    parser.add_argument("--carry-equity-between-sessions", action="store_true",
                        help="carry each completed session's ending equity into the next session")
    parser.add_argument("--with-active-ai-models", action="store_true",
                        help="require approved active KR models for every supplied symbol")
    parser.add_argument("--registry", type=Path, default=Path(".local/models"),
                        help="local model registry used with --with-active-ai-models")
    args = parser.parse_args()
    try:
        require_cli_operational_market("KR", "portfolio quant backtest")
    except ValueError as exc:
        parser.error(str(exc))

    symbols = {symbol for symbol, _path in args.dataset}
    if len(symbols) < 2:
        parser.error("portfolio backtest requires at least two distinct KR symbols")
    costs = (args.buy_commission_bps, args.sell_commission_bps, args.sell_tax_bps, args.slippage_bps)
    if any(not value.is_finite() or value < 0 for value in costs) or sum(costs) <= 0:
        parser.error("cost assumptions must be finite, non-negative, and at least one must be positive")
    if args.slippage_scenario_bps is not None and (
            any(not value.is_finite() or value < 0 or value > Decimal("10000")
                for value in args.slippage_scenario_bps)
            or len(set(args.slippage_scenario_bps)) != len(args.slippage_scenario_bps)):
        parser.error("slippage scenarios must be unique, finite values between zero and 10000 bps")
    if (not args.starting_equity.is_finite() or args.starting_equity <= 0
            or not args.max_order_notional.is_finite() or args.max_order_notional <= 0
            or not args.minimum_turnover.is_finite() or args.minimum_turnover < 0
            or not args.maximum_spread_bps.is_finite() or args.maximum_spread_bps < 0):
        parser.error("capital, order notional, and strategy thresholds are invalid")

    data: dict[str, dict[date, list]] = {}
    dataset_sources: dict[str, list[dict[str, object]]] = {}
    for symbol, csv_path in args.dataset:
        try:
            snapshot = csv_path.read_bytes()
            provenance = load_data_provenance(csv_path, purpose="backtesting", source_bytes=snapshot)
            sessions = load_minute_bar_sessions(csv_path, market="KR", expected_symbol=symbol,
                                                source_bytes=snapshot)
        except (OSError, ValueError) as exc:
            parser.error(f"{symbol} dataset rejected: {exc}")
        symbol_sessions = data.setdefault(symbol, {})
        duplicate_sessions = set(symbol_sessions).intersection(sessions)
        if duplicate_sessions:
            parser.error(f"{symbol} input files contain duplicate KR session dates")
        symbol_sessions.update(sessions)
        dataset_sources.setdefault(symbol, []).append({
            "file_name": csv_path.name,
            "dataset_sha256": hashlib.sha256(snapshot).hexdigest(),
            "data_rights_verified": True,
            "data_provenance": provenance,
            "session_count": len(sessions),
            "bar_count": sum(map(len, sessions.values())),
        })

    dataset_records: dict[str, dict[str, object]] = {}
    for symbol in sorted(data):
        sessions = data[symbol]
        sources = sorted(dataset_sources[symbol], key=lambda source: str(source["file_name"]))
        dataset_records[symbol] = {
            # Preserve the original single-file fields while exposing the full
            # parent-file chain for multi-session runs.
            "dataset_sha256": sources[0]["dataset_sha256"] if len(sources) == 1 else None,
            "data_rights_verified": all(source["data_rights_verified"] is True for source in sources),
            "data_provenance": sources[0]["data_provenance"] if len(sources) == 1 else None,
            "source_files": sources,
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
    active_filters = {}
    active_model_ids = {}
    if args.with_active_ai_models:
        registry = LocalModelRegistry(args.registry)
        for symbol in sorted(data):
            active = registry.active_approved_entry_filter("KR", symbol)
            if active is None:
                parser.error(f"no approved active KR AI model is available for {symbol}")
            model_id, entry_filter = active
            try:
                registry.validate_model_backtest_sessions(model_id, reference_sessions)
            except (OSError, ValueError, KeyError) as exc:
                parser.error(str(exc))
            active_filters[symbol] = entry_filter
            active_model_ids[symbol] = model_id

    def ai_entry_filter(symbol: str, history: list) -> bool:
        return active_filters[symbol](history)

    results = []
    portfolio_results = []
    session_equity = args.starting_equity
    session_consecutive_losses = 0
    for session in sorted(reference_sessions):
        bars = {symbol: data[symbol][session] for symbol in sorted(data)}
        portfolio = backtest_domestic_portfolio(
            bars, starting_equity=session_equity if args.carry_equity_between_sessions else args.starting_equity,
            max_order_notional=args.max_order_notional, config=config,
            initial_consecutive_losses=session_consecutive_losses if args.carry_equity_between_sessions else 0,
            entry_filter=ai_entry_filter if args.with_active_ai_models else None,
        )
        portfolio_results.append(portfolio)
        results.append({
            "date": session.isoformat(),
            "input_bars_by_symbol": {symbol: len(bars[symbol]) for symbol in sorted(bars)},
            "quant_signals": portfolio.quant_signals_by_symbol,
            "strategy_signals": portfolio.signals_by_symbol,
            "ai_rejections": portfolio.ai_rejections_by_symbol,
            "accepted_trades": [{
                "symbol": trade.symbol, "entry_time": trade.entry_time.isoformat(),
                "exit_time": trade.exit_time.isoformat(), "quantity": trade.quantity,
                "entry_notional": str(trade.entry_notional),
                "gross_pnl": str(trade.gross_pnl), "estimated_costs": str(trade.costs),
                "estimated_costs_by_component": {
                    name: str(amount) for name, amount in trade.costs_by_component.items()
                },
                "net_pnl": str(trade.net_pnl), "exit_reason": trade.exit_reason,
            } for trade in portfolio.trades],
            "trade_metrics": summarize_net_trade_pnls(
                [trade.net_pnl for trade in portfolio.trades],
                [trade.entry_notional for trade in portfolio.trades],
            ),
            "rejected_entries": [
                {"symbol": symbol, "reason": reason} for symbol, reason in portfolio.rejected_entries
            ],
            "starting_equity": str(portfolio.starting_equity),
            "ending_equity": str(portfolio.ending_equity),
            "gross_pnl": str(portfolio.gross_pnl),
            "estimated_costs": str(portfolio.estimated_costs),
            "estimated_costs_by_component": {
                name: str(amount) for name, amount in portfolio.estimated_costs_by_component.items()
            },
            "net_pnl": str(portfolio.net_pnl),
            "maximum_open_positions": portfolio.maximum_open_positions,
            "daily_loss_limit_hit": portfolio.daily_loss_limit_hit,
            "consecutive_loss_limit_hit": portfolio.consecutive_loss_limit_hit,
            "starting_consecutive_losses": session_consecutive_losses if args.carry_equity_between_sessions else 0,
            "ending_consecutive_losses": portfolio.ending_consecutive_losses,
            "max_bar_marked_drawdown_amount": str(portfolio.max_bar_marked_drawdown_amount),
            "max_bar_marked_drawdown_percent": str(portfolio.max_bar_marked_drawdown_percent),
            "minimum_bar_marked_equity": str(portfolio.minimum_bar_marked_equity),
            "maximum_bar_marked_equity": str(portfolio.maximum_bar_marked_equity),
            "bar_marked_equity_observations": portfolio.bar_marked_equity_observations,
            "bar_marked_equity_basis": portfolio.bar_marked_equity_basis,
            "executable": portfolio.executable,
        })
        if args.carry_equity_between_sessions:
            session_equity = portfolio.ending_equity
            session_consecutive_losses = portfolio.ending_consecutive_losses

    gross_pnl = sum((Decimal(row["gross_pnl"]) for row in results), Decimal(0))
    estimated_costs = sum((Decimal(row["estimated_costs"]) for row in results), Decimal(0))
    net_pnl = sum((Decimal(row["net_pnl"]) for row in results), Decimal(0))
    ending_equity = session_equity if args.carry_equity_between_sessions else None
    compounded_drawdown = (
        aggregate_compounded_drawdown(args.starting_equity, portfolio_results)
        if args.carry_equity_between_sessions else None
    )
    trade_metrics = {
        "portfolio": summarize_net_trade_pnls(
            [trade.net_pnl for result in portfolio_results for trade in result.trades],
            [trade.entry_notional for result in portfolio_results for trade in result.trades],
        ),
        "by_symbol": {
            symbol: summarize_net_trade_pnls([
                trade.net_pnl for result in portfolio_results for trade in result.trades
                if trade.symbol == symbol
            ], [
                trade.entry_notional for result in portfolio_results for trade in result.trades
                if trade.symbol == symbol
            ])
            for symbol in sorted(data)
        },
    }
    slippage_sensitivity = None
    if args.slippage_scenario_bps is not None:
        sessions_by_date = {
            session: {symbol: data[symbol][session] for symbol in sorted(data)}
            for session in sorted(reference_sessions)
        }
        slippage_sensitivity = [
            {
                "slippage_bps_per_fill": str(item.slippage_bps_per_fill),
                "session_count": item.session_count,
                "accepted_trade_count": item.accepted_trade_count,
                "gross_pnl": str(item.gross_pnl),
                "estimated_costs": str(item.estimated_costs),
                "net_pnl": str(item.net_pnl),
                "ending_equity": str(item.ending_equity) if item.ending_equity is not None else None,
                "maximum_drawdown_amount": str(item.maximum_drawdown_amount),
                "maximum_drawdown_percent": str(item.maximum_drawdown_percent),
                "drawdown_basis": item.drawdown_basis,
                "executable": item.executable,
            }
            for item in analyze_slippage_sensitivity(
                sessions_by_date,
                starting_equity=args.starting_equity,
                max_order_notional=args.max_order_notional,
                config=config,
                slippage_scenarios_bps=args.slippage_scenario_bps,
                carry_equity_between_sessions=args.carry_equity_between_sessions,
                entry_filter=ai_entry_filter if args.with_active_ai_models else None,
            )
        ]

    print(json.dumps({
        "market": "KR",
        "datasets": dataset_records,
        "ai": {
            "enabled": args.with_active_ai_models,
            "active_model_ids": active_model_ids,
        },
        "sessions": results,
        "summary": {
            "session_count": len(results),
            "equity_mode": "compounded_sequential_sessions" if args.carry_equity_between_sessions
            else "independent_sessions",
            "starting_equity": str(args.starting_equity),
            "ending_equity": str(ending_equity) if ending_equity is not None else None,
            "ending_equity_note": None if ending_equity is not None else
            "Independent sessions do not produce a single sequential ending-equity value.",
            "portfolio_return_percent": (
                str((ending_equity - args.starting_equity) * Decimal(100) / args.starting_equity)
                if ending_equity is not None else None
            ),
            "portfolio_return_basis": (
                "compounded sequential ending equity divided by starting equity minus one"
                if ending_equity is not None else None
            ),
            "portfolio_return_note": None if ending_equity is not None else
            "Independent sessions do not produce one portfolio return percentage.",
            "gross_pnl": str(gross_pnl),
            "estimated_costs": str(estimated_costs),
            "estimated_costs_by_component": {
                name: str(sum((Decimal(row["estimated_costs_by_component"][name]) for row in results), Decimal(0)))
                for name in next(iter(results), {}).get("estimated_costs_by_component", {})
            },
            "net_pnl": str(net_pnl),
            "max_bar_marked_drawdown_amount": (
                str(compounded_drawdown.maximum_amount) if compounded_drawdown else None
            ),
            "max_bar_marked_drawdown_percent": (
                str(compounded_drawdown.maximum_percent) if compounded_drawdown else None
            ),
            "bar_marked_equity_observations": (
                compounded_drawdown.observations if compounded_drawdown else None
            ),
            **({
                "starting_equity_per_session": str(args.starting_equity),
                "gross_pnl_across_independent_sessions": str(gross_pnl),
                "estimated_costs_across_independent_sessions": str(estimated_costs),
                "estimated_costs_by_component_across_independent_sessions": {
                    name: str(sum((Decimal(row["estimated_costs_by_component"][name]) for row in results), Decimal(0)))
                    for name in next(iter(results), {}).get("estimated_costs_by_component", {})
                },
                "net_pnl_across_independent_sessions": str(net_pnl),
            } if not args.carry_equity_between_sessions else {}),
            "accepted_trade_count": sum(len(row["accepted_trades"]) for row in results),
            "trade_metrics": trade_metrics,
            "executable": False,
        },
        "cost_assumptions_bps_per_fill": {
            "buy_commission": str(args.buy_commission_bps),
            "sell_commission": str(args.sell_commission_bps),
            "sell_tax": str(args.sell_tax_bps),
            "slippage": str(args.slippage_bps),
        },
        "slippage_sensitivity": slippage_sensitivity,
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
            "does not condition on other open positions. "
            + ("Completed session equity and consecutive losing-trade count carry into the next session; "
               "daily loss limits reset each session."
               if args.carry_equity_between_sessions else
               "Each date is an independent, non-compounded session.")
        ),
        "executable": False,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
