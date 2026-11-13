"""Domestic-only portfolio strategy replay. The result can never authorize orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Callable

from .quant import (
    MinuteBar, QuantConfig, _atr, _cost, _validate_session, _vwap,
    exchange_session_date, orb_vwap_entry_candidate, risk_bounded_quantity,
)

_PORTFOLIO_TOTAL_TOLERANCE = Decimal("1e-18")


@dataclass(frozen=True)
class PortfolioTrade:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    quantity: int
    gross_pnl: Decimal
    costs: Decimal
    net_pnl: Decimal
    exit_reason: str


@dataclass(frozen=True)
class PortfolioBacktestResult:
    market: str
    session: date
    starting_equity: Decimal
    ending_equity: Decimal
    gross_pnl: Decimal
    estimated_costs: Decimal
    net_pnl: Decimal
    trades: tuple[PortfolioTrade, ...]
    quant_signals_by_symbol: dict[str, int]
    signals_by_symbol: dict[str, int]
    ai_rejections_by_symbol: dict[str, int]
    rejected_entries: tuple[tuple[str, str], ...]
    maximum_open_positions: int
    daily_loss_limit_hit: bool
    consecutive_loss_limit_hit: bool
    executable: bool = False


@dataclass(frozen=True)
class _PendingEntry:
    stop_distance: Decimal
    signal_spread_bps: Decimal


@dataclass
class _OpenPosition:
    entry_index: int
    raw_entry_price: Decimal
    fill_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    quantity: int
    cash_debit: Decimal


def backtest_domestic_portfolio(
    bars_by_symbol: dict[str, list[MinuteBar]], *, starting_equity: Decimal,
    max_order_notional: Decimal, config: QuantConfig = QuantConfig(),
    max_open_positions: int = 3, max_daily_loss_pct: Decimal = Decimal("1"),
    max_consecutive_losses: int = 3,
    entry_filter: Callable[[str, list[MinuteBar]], bool] | None = None,
) -> PortfolioBacktestResult:
    """Generate synchronized symbol signals and replay them against shared portfolio state.

    Signals are evaluated from each symbol's completed bars. Entries execute at the
    following bar's open; at that open, prior-open positions still consume slots and
    capital before exits in the same minute are applied. This prevents look-ahead and
    optimistic same-minute reuse. Daily loss only blocks future entries; open trades
    continue to receive stop/target/session-end management. This is not an order path.
    """
    if not isinstance(starting_equity, Decimal) or not starting_equity.is_finite() or starting_equity <= 0:
        raise ValueError("starting equity must be a positive finite decimal")
    if not isinstance(max_order_notional, Decimal) or not max_order_notional.is_finite() or max_order_notional <= 0:
        raise ValueError("maximum order notional must be a positive finite decimal")
    if not 1 <= max_open_positions <= 3 or not 1 <= max_consecutive_losses <= 3:
        raise ValueError("portfolio position and loss-streak limits may not exceed approved limits")
    if not isinstance(max_daily_loss_pct, Decimal) or not max_daily_loss_pct.is_finite() or not Decimal(0) < max_daily_loss_pct <= Decimal("1"):
        raise ValueError("daily loss limit must be positive and no more than one percent")
    if not bars_by_symbol:
        raise ValueError("at least one domestic symbol dataset is required")

    if any(not isinstance(symbol, str) for symbol in bars_by_symbol):
        raise ValueError("portfolio replay requires six-digit domestic ticker symbols")
    normalized_bars = {symbol.strip().upper(): bars for symbol, bars in bars_by_symbol.items()}
    if (len(normalized_bars) != len(bars_by_symbol) or any(not symbol for symbol in normalized_bars)
            or len(normalized_bars) < 2):
        raise ValueError("portfolio replay requires at least two uniquely named symbols")
    if any(len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit()
           for symbol in normalized_bars):
        raise ValueError("portfolio replay requires six-digit domestic ticker symbols")

    ordered_symbols = sorted(normalized_bars)
    first_bars = normalized_bars[ordered_symbols[0]]
    _validate_session(first_bars, "KR", require_complete=True)
    session = exchange_session_date(first_bars[0].timestamp, "KR")
    timestamps = [bar.timestamp for bar in first_bars]
    for symbol in ordered_symbols[1:]:
        bars = normalized_bars[symbol]
        _validate_session(bars, "KR", require_complete=True)
        if exchange_session_date(bars[0].timestamp, "KR") != session:
            raise ValueError("all portfolio symbols must belong to the same KR session")
        if [bar.timestamp for bar in bars] != timestamps:
            raise ValueError("all portfolio symbols must have the same complete minute timestamps")

    minimum_history = max(config.ema_period, config.atr_period,
                          config.relative_volume_period + 1, config.opening_range_bars)
    pending: dict[str, _PendingEntry] = {}
    positions: dict[str, _OpenPosition] = {}
    signals = {symbol: 0 for symbol in ordered_symbols}
    quant_signals = {symbol: 0 for symbol in ordered_symbols}
    ai_rejections = {symbol: 0 for symbol in ordered_symbols}
    rejected: list[tuple[str, str]] = []
    completed: list[PortfolioTrade] = []
    cash = starting_equity
    realized = Decimal(0)
    loss_streak = 0
    daily_loss_hit = False
    streak_hit = False
    maximum_open = 0
    daily_loss_budget = starting_equity * max_daily_loss_pct / Decimal(100)

    def marked_pnl(index: int, *, price_field: str) -> Decimal:
        value = Decimal(0)
        for symbol, position in positions.items():
            price = getattr(normalized_bars[symbol][index], price_field)
            liquidation_bps = (config.effective_sell_commission_bps + config.sell_tax_bps
                               + config.slippage_bps + config.maximum_spread_bps / Decimal(2))
            proceeds = price * (Decimal(1) - liquidation_bps / Decimal(10_000)) * position.quantity
            value += proceeds - position.cash_debit
        return value

    for index, timestamp in enumerate(timestamps):
        # Check open-to-open gaps before placing new entries for this minute.
        if realized + marked_pnl(index, price_field="open") <= -daily_loss_budget:
            daily_loss_hit = True

        for symbol in ordered_symbols:
            intent = pending.pop(symbol, None)
            if intent is None:
                continue
            if daily_loss_hit:
                rejected.append((symbol, "DAILY_LOSS_LIMIT"))
                continue
            if streak_hit:
                rejected.append((symbol, "CONSECUTIVE_LOSS_LIMIT"))
                continue
            if symbol in positions:
                rejected.append((symbol, "SYMBOL_ALREADY_OPEN"))
                continue
            if len(positions) >= max_open_positions:
                rejected.append((symbol, "MAX_OPEN_POSITIONS"))
                continue
            bar = normalized_bars[symbol][index]
            entry_cost_bps = config.slippage_bps + intent.signal_spread_bps / Decimal(2)
            fill = bar.open * (Decimal(1) + entry_cost_bps / Decimal(10_000))
            equity_now = max(Decimal(1), starting_equity + realized + marked_pnl(index, price_field="open"))
            quantity = risk_bounded_quantity(
                entry_price=fill, stop_distance=intent.stop_distance, equity=equity_now,
                max_order_notional=max_order_notional,
                buy_commission_bps=config.effective_buy_commission_bps,
                sell_commission_bps=config.effective_sell_commission_bps,
                sell_tax_bps=config.sell_tax_bps, slippage_bps=config.slippage_bps,
                maximum_spread_bps=intent.signal_spread_bps,
            )
            debit_per_share = fill * (Decimal(1) + config.effective_buy_commission_bps / Decimal(10_000))
            quantity = min(quantity, int(cash / debit_per_share))
            if quantity <= 0:
                rejected.append((symbol, "NO_SHARES_WITHIN_RISK_OR_CASH_LIMIT"))
                continue
            distance = intent.stop_distance
            debit = debit_per_share * quantity
            cash -= debit
            positions[symbol] = _OpenPosition(
                entry_index=index, raw_entry_price=bar.open, fill_price=fill,
                stop_price=fill - distance,
                target_price=fill + distance * config.reward_risk_multiple,
                quantity=quantity, cash_debit=debit,
            )
            maximum_open = max(maximum_open, len(positions))

        exited_this_bar: set[str] = set()
        for symbol in ordered_symbols:
            position = positions.get(symbol)
            if position is None:
                continue
            bars = normalized_bars[symbol]
            bar = bars[index]
            exit_price: Decimal | None = None
            reason = ""
            if index > position.entry_index and bars[index - 1].close < _vwap(bars[:index]):
                exit_price, reason = bar.open, "VWAP_LOSS"
            elif bar.open <= position.stop_price or bar.low <= position.stop_price:
                exit_price, reason = min(bar.open, position.stop_price), "ATR_STOP"
            elif bar.open >= position.target_price:
                exit_price, reason = bar.open, "TAKE_PROFIT_GAP"
            elif bar.high >= position.target_price:
                exit_price, reason = position.target_price, "TAKE_PROFIT"
            elif index == len(bars) - 1:
                exit_price, reason = bar.close, "SESSION_END"
            if exit_price is None:
                continue

            spread_bar = bar if reason == "SESSION_END" else bars[index - 1]
            observed_spread = (spread_bar.spread_bps if spread_bar.spread_observed_at is not None
                               else config.maximum_spread_bps)
            exit_cost_bps = config.slippage_bps + observed_spread / Decimal(2)
            slipped_exit = exit_price * (Decimal(1) - exit_cost_bps / Decimal(10_000))
            quantity = position.quantity
            gross = (exit_price - position.raw_entry_price) * quantity
            execution_costs = ((position.fill_price - position.raw_entry_price)
                               + (exit_price - slipped_exit)) * quantity
            commission_costs = (
                _cost(position.fill_price, quantity, config.effective_buy_commission_bps)
                + _cost(slipped_exit, quantity, config.effective_sell_commission_bps + config.sell_tax_bps)
            )
            costs = execution_costs + commission_costs
            net_pnl = gross - costs
            completed.append(PortfolioTrade(
                symbol, timestamps[position.entry_index], timestamp, quantity,
                gross, costs, net_pnl, reason,
            ))
            realized += net_pnl
            cash += position.cash_debit + net_pnl
            del positions[symbol]
            exited_this_bar.add(symbol)
            loss_streak = loss_streak + 1 if net_pnl < 0 else 0
            if loss_streak >= max_consecutive_losses:
                streak_hit = True

        if realized + marked_pnl(index, price_field="close") <= -daily_loss_budget:
            daily_loss_hit = True

        # Signal from this completed bar; execution is deferred to the following bar open.
        if index + 1 < len(timestamps):
            for symbol in ordered_symbols:
                if symbol in positions or symbol in exited_this_bar:
                    continue
                history = normalized_bars[symbol][:index + 1]
                if len(history) < minimum_history or not orb_vwap_entry_candidate(history, config, market="KR"):
                    continue
                quant_signals[symbol] += 1
                if entry_filter is not None:
                    ai_approved = entry_filter(symbol, history)
                    if not isinstance(ai_approved, bool):
                        raise ValueError("AI entry filter must return a boolean decision")
                    if not ai_approved:
                        ai_rejections[symbol] += 1
                        continue
                signal_bar = history[-1]
                if signal_bar.spread_bps is None or signal_bar.spread_observed_at is None:
                    continue
                signals[symbol] += 1
                pending[symbol] = _PendingEntry(
                    stop_distance=_atr(history, config.atr_period) * config.atr_stop_multiple,
                    signal_spread_bps=signal_bar.spread_bps,
                )

    if positions:
        raise ValueError("all domestic portfolio positions must be closed by the session end")
    gross_pnl = sum((trade.gross_pnl for trade in completed), Decimal(0))
    estimated_costs = sum((trade.costs for trade in completed), Decimal(0))
    trade_net_pnl = sum((trade.net_pnl for trade in completed), Decimal(0))
    if (abs(gross_pnl - estimated_costs - trade_net_pnl) > _PORTFOLIO_TOTAL_TOLERANCE
            or abs(trade_net_pnl - realized) > _PORTFOLIO_TOTAL_TOLERANCE):
        raise ValueError("portfolio totals do not reconcile to completed trade results")
    return PortfolioBacktestResult(
        market="KR", session=session, starting_equity=starting_equity,
        ending_equity=starting_equity + realized, gross_pnl=gross_pnl,
        estimated_costs=estimated_costs, net_pnl=realized,
        trades=tuple(completed), quant_signals_by_symbol=quant_signals,
        signals_by_symbol=signals, ai_rejections_by_symbol=ai_rejections,
        rejected_entries=tuple(rejected), maximum_open_positions=maximum_open,
        daily_loss_limit_hit=daily_loss_hit, consecutive_loss_limit_hit=streak_hit,
        executable=False,
    )
