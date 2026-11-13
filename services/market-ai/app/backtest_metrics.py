"""Cost-adjusted, non-compounded summaries for deterministic backtests."""

from __future__ import annotations

from decimal import Decimal
from statistics import median
from typing import Sequence

from .quant import BacktestResult, MinuteBar, Trade


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / Decimal(len(values)) if values else None


def summarize_trades(trades: Sequence[Trade]) -> dict[str, object]:
    """Summarize trade returns against entry notional, without compounding days."""
    returns_bps: list[Decimal] = []
    gross_returns_bps: list[Decimal] = []
    cost_returns_bps: list[Decimal] = []
    for trade in trades:
        numeric_values = (trade.entry_price, trade.gross_pnl, trade.costs, trade.net_pnl)
        if (trade.quantity <= 0 or any(not value.is_finite() for value in numeric_values)
                or trade.entry_price <= 0):
            raise ValueError("backtest trades require positive quantity and entry price")
        notional = trade.entry_price * trade.quantity
        gross_returns_bps.append(trade.gross_pnl * Decimal(10_000) / notional)
        cost_returns_bps.append(trade.costs * Decimal(10_000) / notional)
        returns_bps.append(trade.net_pnl * Decimal(10_000) / notional)

    winners = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losers = [trade.net_pnl for trade in trades if trade.net_pnl < 0]
    loss_total = abs(sum(losers, Decimal(0)))
    profit_factor = sum(winners, Decimal(0)) / loss_total if loss_total else None
    win_rate = Decimal(len(winners)) * Decimal(100) / Decimal(len(trades)) if trades else None
    mean_gross = _mean(gross_returns_bps)
    mean_cost = _mean(cost_returns_bps)
    mean_net = _mean(returns_bps)

    profit_factor_status = (
        "defined" if loss_total else
        "no_losing_trades" if winners else
        "break_even_only" if trades else
        "no_trades"
    )
    return {
        "trade_count": len(trades),
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate_percent": None if win_rate is None else str(win_rate),
        "profit_factor_net": None if profit_factor is None else str(profit_factor),
        "profit_factor_status": profit_factor_status,
        "mean_gross_return_bps": None if mean_gross is None else str(mean_gross),
        "mean_cost_bps": None if mean_cost is None else str(mean_cost),
        "mean_net_return_bps": None if mean_net is None else str(mean_net),
        "median_net_return_bps": None if not returns_bps else str(median(returns_bps)),
        "worst_trade_net_return_bps": None if not returns_bps else str(min(returns_bps)),
        "return_basis": "arithmetic per trade: net PnL divided by entry price times quantity; not compounded",
    }


def summarize_sessions(sessions: Sequence[BacktestResult]) -> dict[str, object]:
    """Summarize per-session PnL, including no-trade sessions as zero outcomes."""
    if len({(session.market, session.symbol, session.session) for session in sessions}) != len(sessions):
        raise ValueError("backtest session summaries must have unique market, symbol, and date keys")
    net_values: list[Decimal] = []
    traded_sessions = 0
    for session in sessions:
        values = (session.gross_pnl, session.costs, session.net_pnl)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
            raise ValueError("backtest session PnL values must be finite decimals")
        if session.net_pnl != session.gross_pnl - session.costs:
            raise ValueError("backtest session net PnL must equal gross PnL less costs")
        for trade in session.trades:
            trade_values = (trade.gross_pnl, trade.costs, trade.net_pnl)
            if (any(not isinstance(value, Decimal) or not value.is_finite() for value in trade_values)
                    or trade.net_pnl != trade.gross_pnl - trade.costs):
                raise ValueError("backtest session contains inconsistent trade PnL")
        if (sum((trade.gross_pnl for trade in session.trades), Decimal(0)) != session.gross_pnl
                or sum((trade.costs for trade in session.trades), Decimal(0)) != session.costs
                or sum((trade.net_pnl for trade in session.trades), Decimal(0)) != session.net_pnl):
            raise ValueError("backtest session totals must match its trades")
        net_values.append(session.net_pnl)
        if session.trades:
            traded_sessions += 1

    return {
        "session_count": len(sessions),
        "sessions_with_trades": traded_sessions,
        "sessions_without_trades": len(sessions) - traded_sessions,
        "positive_net_pnl_sessions": sum(value > 0 for value in net_values),
        "negative_net_pnl_sessions": sum(value < 0 for value in net_values),
        "zero_net_pnl_sessions": sum(value == 0 for value in net_values),
        "mean_net_pnl_per_session": None if not net_values else str(_mean(net_values)),
        "median_net_pnl_per_session": None if not net_values else str(median(net_values)),
        "worst_session_net_pnl": None if not net_values else str(min(net_values)),
        "best_session_net_pnl": None if not net_values else str(max(net_values)),
        "pnl_basis": "currency PnL per independent session; no capital compounding",
    }


def summarize_spread_coverage(bars: Sequence[MinuteBar]) -> dict[str, object]:
    """Separate numeric spreads from time-verified spread inputs used by the strategy."""
    total = len(bars)
    observed = sum(bar.spread_bps is not None for bar in bars)
    timestamped = sum(
        bar.spread_bps is not None and bar.spread_observed_at is not None
        for bar in bars
    )
    return {
        "spread_bars": observed,
        "spread_coverage_percent": round(100 * observed / total, 2) if total else None,
        "spread_observation_time_bars": timestamped,
        "spread_observation_time_coverage_percent": (
            round(100 * timestamped / total, 2) if total else None
        ),
        "spread_filter_has_complete_input": total > 0 and timestamped == total,
    }
