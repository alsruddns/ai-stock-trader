"""Cost-adjusted, non-compounded summaries for deterministic backtests."""

from __future__ import annotations

from decimal import Decimal
from statistics import median
from typing import Sequence

from .quant import Trade


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
