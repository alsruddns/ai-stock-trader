"""Cost-adjusted, non-compounded summaries for deterministic backtests."""

from __future__ import annotations

from decimal import Decimal
from statistics import median
from typing import Sequence

from .quant import BacktestResult, MinuteBar, Trade

_SESSION_TOTAL_TOLERANCE = Decimal("1e-18")


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / Decimal(len(values)) if values else None


def _amounts_match(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= _SESSION_TOTAL_TOLERANCE


def _validate_trade(trade: Trade) -> None:
    if isinstance(trade.quantity, bool) or not isinstance(trade.quantity, int) or trade.quantity <= 0:
        raise ValueError("backtest trades require a positive integer quantity")
    numeric_values = (trade.entry_price, trade.exit_price, trade.gross_pnl, trade.costs, trade.net_pnl)
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in numeric_values):
        raise ValueError("backtest trade values must be finite decimals")
    if trade.entry_price <= 0 or trade.exit_price <= 0:
        raise ValueError("backtest trades require positive entry and exit prices")
    if trade.costs < 0:
        raise ValueError("backtest trade costs must be non-negative")
    if trade.net_pnl != trade.gross_pnl - trade.costs:
        raise ValueError("backtest trade net PnL must equal gross PnL less costs")


def summarize_trades(trades: Sequence[Trade]) -> dict[str, object]:
    """Summarize trade returns against entry notional, without compounding days."""
    returns_bps: list[Decimal] = []
    gross_returns_bps: list[Decimal] = []
    cost_returns_bps: list[Decimal] = []
    for trade in trades:
        _validate_trade(trade)
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


def summarize_net_trade_pnls(net_pnls: Sequence[Decimal]) -> dict[str, object]:
    """Summarize portfolio trade outcomes after modeled costs, in KRW amounts."""
    values = list(net_pnls)
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise ValueError("portfolio net trade PnL values must be finite decimals")

    winners = [value for value in values if value > 0]
    losers = [value for value in values if value < 0]
    breakeven_count = len(values) - len(winners) - len(losers)
    loss_total = abs(sum(losers, Decimal(0)))
    profit_factor = sum(winners, Decimal(0)) / loss_total if loss_total else None
    average = sum(values, Decimal(0)) / Decimal(len(values)) if values else None
    average_winner = sum(winners, Decimal(0)) / Decimal(len(winners)) if winners else None
    average_loser = sum(losers, Decimal(0)) / Decimal(len(losers)) if losers else None
    status = (
        "defined" if loss_total else
        "no_losing_trades" if winners else
        "break_even_only" if values else
        "no_trades"
    )
    return {
        "trade_count": len(values),
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "breakeven_trades": breakeven_count,
        "win_rate_percent": str(Decimal(len(winners)) * Decimal(100) / Decimal(len(values)))
        if values else None,
        "profit_factor_net": str(profit_factor) if profit_factor is not None else None,
        "profit_factor_status": status,
        "mean_net_pnl_krw": str(average) if average is not None else None,
        "median_net_pnl_krw": str(median(values)) if values else None,
        "mean_winning_trade_net_pnl_krw": str(average_winner) if average_winner is not None else None,
        "mean_losing_trade_net_pnl_krw": str(average_loser) if average_loser is not None else None,
        "pnl_basis": "KRW per completed trade after configured modeled costs; not a return rate",
    }


def session_net_return_bps(session: BacktestResult) -> Decimal | None:
    """Return a session's net PnL over its traded entry notional, or None if flat."""
    if not session.trades:
        return None
    traded_notional = Decimal(0)
    for trade in session.trades:
        _validate_trade(trade)
        traded_notional += trade.entry_price * trade.quantity
    if traded_notional <= 0 or not traded_notional.is_finite():
        raise ValueError("backtest session traded notional must be finite and positive")
    return session.net_pnl * Decimal(10_000) / traded_notional


def summarize_sessions(sessions: Sequence[BacktestResult]) -> dict[str, object]:
    """Summarize per-session PnL, including no-trade sessions as zero outcomes."""
    if len({(session.market, session.symbol, session.session) for session in sessions}) != len(sessions):
        raise ValueError("backtest session summaries must have unique market, symbol, and date keys")
    net_values: list[Decimal] = []
    return_bps_values: list[Decimal] = []
    traded_sessions = 0
    for session in sessions:
        values = (session.gross_pnl, session.costs, session.net_pnl)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
            raise ValueError("backtest session PnL values must be finite decimals")
        if session.net_pnl != session.gross_pnl - session.costs:
            raise ValueError("backtest session net PnL must equal gross PnL less costs")
        if session.costs < 0:
            raise ValueError("backtest session costs must be non-negative")
        if not session.trades and any(value != 0 for value in values):
            raise ValueError("backtest sessions without trades must have zero PnL")
        for trade in session.trades:
            _validate_trade(trade)
        trade_gross = sum((trade.gross_pnl for trade in session.trades), Decimal(0))
        trade_costs = sum((trade.costs for trade in session.trades), Decimal(0))
        trade_net = sum((trade.net_pnl for trade in session.trades), Decimal(0))
        # Decimal operations are rounded at the active precision. Equivalent totals
        # can therefore differ in sub-atto currency fractions after repeated trades.
        if (not _amounts_match(trade_gross, session.gross_pnl)
                or not _amounts_match(trade_costs, session.costs)
                or not _amounts_match(trade_net, session.net_pnl)):
            raise ValueError("backtest session totals must match its trades")
        net_values.append(session.net_pnl)
        if session.trades:
            traded_sessions += 1
            return_bps = session_net_return_bps(session)
            if return_bps is None:
                raise ValueError("traded sessions must have a calculated net return")
            return_bps_values.append(return_bps)

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
        "sessions_with_return": len(return_bps_values),
        "mean_net_return_bps_on_traded_notional": (
            None if not return_bps_values else str(_mean(return_bps_values))
        ),
        "median_net_return_bps_on_traded_notional": (
            None if not return_bps_values else str(median(return_bps_values))
        ),
        "worst_session_net_return_bps_on_traded_notional": (
            None if not return_bps_values else str(min(return_bps_values))
        ),
        "best_session_net_return_bps_on_traded_notional": (
            None if not return_bps_values else str(max(return_bps_values))
        ),
        "pnl_basis": "currency PnL per independent session; no capital compounding",
        "return_basis": "net currency PnL divided by summed entry notional of traded shares; no-trade sessions excluded",
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
