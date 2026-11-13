"""Domestic-only, non-executable portfolio replay for candidate quant trades."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .quant import BacktestResult, MinuteBar, QuantConfig, Trade, _validate_session, risk_bounded_quantity


@dataclass(frozen=True)
class PortfolioTrade:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    quantity: int
    net_pnl: Decimal
    exit_reason: str


@dataclass(frozen=True)
class PortfolioBacktestResult:
    market: str
    session: date
    starting_equity: Decimal
    ending_equity: Decimal
    net_pnl: Decimal
    trades: tuple[PortfolioTrade, ...]
    rejected_entries: tuple[tuple[str, str], ...]
    maximum_open_positions: int
    daily_loss_limit_hit: bool
    consecutive_loss_limit_hit: bool
    executable: bool = False


@dataclass
class _Position:
    trade: Trade
    quantity: int
    entry_equity_debit: Decimal


def backtest_domestic_portfolio(
    candidates: list[BacktestResult], bars_by_symbol: dict[str, list[MinuteBar]], *,
    starting_equity: Decimal, max_order_notional: Decimal,
    config: QuantConfig = QuantConfig(), max_open_positions: int = 3,
    max_daily_loss_pct: Decimal = Decimal("1"), max_consecutive_losses: int = 3,
) -> PortfolioBacktestResult:
    """Replay baseline one-share candidates under shared capital and domestic risk gates.

    Signals/fills/exits come from independently generated strategy candidates. Entries
    are processed before exits at a shared minute, preventing same-minute proceeds or
    slots from being reused. Portfolio limits only block new entries; open positions
    continue to their candidate exits. This is a research simulation, never an order path.
    """
    if not candidates or not isinstance(starting_equity, Decimal) or not starting_equity.is_finite() or starting_equity <= 0:
        raise ValueError("positive starting equity and at least one candidate are required")
    if not isinstance(max_order_notional, Decimal) or not max_order_notional.is_finite() or max_order_notional <= 0:
        raise ValueError("maximum order notional must be a positive finite decimal")
    if not 1 <= max_open_positions <= 3 or not 1 <= max_consecutive_losses <= 3:
        raise ValueError("portfolio position and loss-streak limits may not exceed approved limits")
    if not isinstance(max_daily_loss_pct, Decimal) or not Decimal(0) < max_daily_loss_pct <= Decimal("1"):
        raise ValueError("daily loss limit must be greater than zero and at most one percent")

    symbols = [candidate.symbol.upper() for candidate in candidates]
    normalized_bars = {symbol.upper(): bars for symbol, bars in bars_by_symbol.items()}
    if (len(normalized_bars) != len(bars_by_symbol) or len(set(symbols)) != len(symbols)
            or set(symbols) != set(normalized_bars)):
        raise ValueError("candidate symbols and bar symbols must match uniquely")
    session = candidates[0].session
    timestamps: set[datetime] = set()
    bars_index: dict[str, dict[datetime, MinuteBar]] = {}
    previous_bar_index: dict[str, dict[datetime, MinuteBar | None]] = {}
    trades_at_entry: dict[datetime, list[tuple[str, Trade]]] = {}
    trades_at_exit: dict[datetime, list[tuple[str, Trade]]] = {}
    for candidate in candidates:
        symbol = candidate.symbol.upper()
        if candidate.market != "KR" or candidate.session != session or candidate.executable:
            raise ValueError("portfolio replay accepts only non-executable KR candidates from one session")
        bars = normalized_bars[symbol]
        _validate_session(bars, "KR", require_complete=True)
        from .quant import exchange_session_date
        if not bars or exchange_session_date(bars[0].timestamp, "KR") != candidate.session:
            raise ValueError("candidate session does not match its bars")
        indexed = {bar.timestamp: bar for bar in bars}
        bars_index[symbol] = indexed
        previous_bar_index[symbol] = {
            bar.timestamp: bars[index - 1] if index > 0 else None
            for index, bar in enumerate(bars)
        }
        timestamps.update(indexed)
        for trade in candidate.trades:
            if trade.quantity != 1 or trade.stop_distance <= 0:
                raise ValueError("portfolio candidates must be baseline one-share trades with a recorded stop distance")
            if trade.entry_time not in indexed or trade.exit_time not in indexed or trade.exit_time < trade.entry_time:
                raise ValueError("candidate trade timestamps must map to its session bars")
            trades_at_entry.setdefault(trade.entry_time, []).append((symbol, trade))
            trades_at_exit.setdefault(trade.exit_time, []).append((symbol, trade))

    cash = starting_equity
    realized = Decimal(0)
    open_positions: dict[str, _Position] = {}
    accepted: list[PortfolioTrade] = []
    rejected: list[tuple[str, str]] = []
    maximum_open = 0
    loss_streak = 0
    daily_loss_hit = False
    streak_hit = False
    daily_loss_budget = starting_equity * max_daily_loss_pct / Decimal(100)

    for timestamp in sorted(timestamps):
        # Entry order is stable by symbol and conservatively precedes exits at the same minute.
        for symbol, trade in sorted(trades_at_entry.get(timestamp, []), key=lambda item: item[0]):
            if daily_loss_hit:
                rejected.append((symbol, "DAILY_LOSS_LIMIT"))
                continue
            if streak_hit:
                rejected.append((symbol, "CONSECUTIVE_LOSS_LIMIT"))
                continue
            if symbol in open_positions:
                rejected.append((symbol, "SYMBOL_ALREADY_OPEN"))
                continue
            if len(open_positions) >= max_open_positions:
                rejected.append((symbol, "MAX_OPEN_POSITIONS"))
                continue
            if not cash > 0:
                rejected.append((symbol, "INSUFFICIENT_SHARED_CASH"))
                continue
            signal_bar = previous_bar_index[symbol][timestamp]
            if signal_bar is None:
                rejected.append((symbol, "NO_PRE_ENTRY_BAR"))
                continue
            qty = risk_bounded_quantity(
                entry_price=trade.entry_price, stop_distance=trade.stop_distance,
                equity=max(Decimal(1), starting_equity + realized),
                max_order_notional=max_order_notional, buy_commission_bps=config.effective_buy_commission_bps,
                sell_commission_bps=config.effective_sell_commission_bps, sell_tax_bps=config.sell_tax_bps,
                slippage_bps=config.slippage_bps,
                maximum_spread_bps=(signal_bar.spread_bps if signal_bar.spread_bps is not None
                                    and signal_bar.spread_observed_at is not None
                                    else config.maximum_spread_bps),
            )
            entry_cost_bps = config.effective_buy_commission_bps
            debit_per_share = trade.entry_price * (Decimal(1) + entry_cost_bps / Decimal(10_000))
            qty = min(qty, int(cash / debit_per_share))
            if qty <= 0:
                rejected.append((symbol, "NO_SHARES_WITHIN_RISK_OR_CASH_LIMIT"))
                continue
            debit = debit_per_share * qty
            cash -= debit
            open_positions[symbol] = _Position(trade, qty, debit)
            maximum_open = max(maximum_open, len(open_positions))

        # Mark positions using each symbol's close at this minute and a conservative liquidation cost.
        unrealized = Decimal(0)
        for symbol, position in open_positions.items():
            bar = bars_index[symbol].get(timestamp)
            if bar is None:
                continue
            liquidation_bps = (config.effective_sell_commission_bps + config.sell_tax_bps
                               + config.slippage_bps + config.maximum_spread_bps / Decimal(2))
            estimated_exit = bar.close * (Decimal(1) - liquidation_bps / Decimal(10_000)) * position.quantity
            unrealized += estimated_exit - position.entry_equity_debit
        if realized + unrealized <= -daily_loss_budget:
            daily_loss_hit = True

        for symbol, trade in sorted(trades_at_exit.get(timestamp, []), key=lambda item: item[0]):
            position = open_positions.pop(symbol, None)
            if position is None:
                continue
            pnl = trade.net_pnl * position.quantity
            realized += pnl
            cash += position.entry_equity_debit + pnl
            accepted.append(PortfolioTrade(symbol, trade.entry_time, trade.exit_time,
                                           position.quantity, pnl, trade.exit_reason))
            loss_streak = loss_streak + 1 if pnl < 0 else 0
            if loss_streak >= max_consecutive_losses:
                streak_hit = True

    # A complete-session strategy must close all positions. Treat an open position as invalid input.
    if open_positions:
        raise ValueError("candidate trades must include a completed same-session exit")
    return PortfolioBacktestResult(
        market="KR", session=session, starting_equity=starting_equity, ending_equity=starting_equity + realized,
        net_pnl=realized, trades=tuple(accepted), rejected_entries=tuple(rejected),
        maximum_open_positions=maximum_open, daily_loss_limit_hit=daily_loss_hit,
        consecutive_loss_limit_hit=streak_hit, executable=False,
    )
