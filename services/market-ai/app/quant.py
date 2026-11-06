"""Deterministic, single-session VWAP/ORB strategy and conservative backtest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Literal
from zoneinfo import ZoneInfo

from .market_calendar import exchange_continuous_session_window

Market = Literal["KR", "US"]


@dataclass(frozen=True)
class MinuteBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    spread_bps: Decimal | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("bar timestamps must include a timezone")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("bar high/low do not contain OHLC prices")
        if self.volume < 0:
            raise ValueError("volume must be non-negative")
        if self.spread_bps is not None and self.spread_bps < 0:
            raise ValueError("spread must be non-negative")


@dataclass(frozen=True)
class QuantConfig:
    opening_range_bars: int = 5
    ema_period: int = 20
    atr_period: int = 14
    relative_volume_period: int = 20
    minimum_relative_volume: Decimal = Decimal("1.5")
    maximum_spread_bps: Decimal = Decimal("15")
    minimum_turnover: Decimal = Decimal("1000000")
    atr_stop_multiple: Decimal = Decimal("1.5")
    reward_risk_multiple: Decimal = Decimal("2")
    commission_bps: Decimal = Decimal("5")
    slippage_bps: Decimal = Decimal("5")

    def __post_init__(self) -> None:
        if min(self.opening_range_bars, self.ema_period, self.atr_period, self.relative_volume_period) <= 0:
            raise ValueError("indicator periods must be positive")
        if self.minimum_relative_volume <= 0 or self.maximum_spread_bps < 0 or self.minimum_turnover < 0:
            raise ValueError("liquidity thresholds are invalid")
        if self.atr_stop_multiple <= 0 or self.reward_risk_multiple <= 0:
            raise ValueError("risk multiples must be positive")
        if min(self.commission_bps, self.slippage_bps) < 0:
            raise ValueError("trading costs cannot be negative")


@dataclass(frozen=True)
class Trade:
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: int
    gross_pnl: Decimal
    costs: Decimal
    net_pnl: Decimal
    exit_reason: str


@dataclass(frozen=True)
class BacktestResult:
    market: Market
    symbol: str
    session: date
    trades: tuple[Trade, ...]
    gross_pnl: Decimal
    costs: Decimal
    net_pnl: Decimal
    data_points: int
    signals: int
    executable: bool = False


def exchange_session_date(timestamp: datetime, market: Market) -> date:
    zones = {"KR": "Asia/Seoul", "US": "America/New_York"}
    try:
        zone = ZoneInfo(zones[market])
    except KeyError as exc:
        raise ValueError("market must be KR or US") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("bar timestamps must include a timezone")
    return timestamp.astimezone(zone).date()


def _validate_session(bars: list[MinuteBar], market: Market, *, require_complete: bool = False) -> None:
    if not bars:
        raise ValueError("at least one bar is required")
    session_date = exchange_session_date(bars[0].timestamp, market)
    if any(exchange_session_date(bar.timestamp, market) != session_date for bar in bars):
        raise ValueError("one session per backtest is supported")
    if any(left.timestamp >= right.timestamp for left, right in zip(bars, bars[1:])):
        raise ValueError("bars must be strictly increasing by timestamp")
    window = exchange_continuous_session_window(market, bars[0].timestamp)
    if window is None:
        raise ValueError("bars must belong to an official exchange session")
    opened, closed = window
    timestamps = [bar.timestamp.astimezone(ZoneInfo("UTC")) for bar in bars]
    if timestamps[0] != opened:
        raise ValueError("backtest bars must begin at the official regular-session open")
    if any(timestamp < opened or timestamp >= closed for timestamp in timestamps):
        raise ValueError("backtest bars must remain within regular trading hours")
    if any(right - left != timedelta(minutes=1) for left, right in zip(timestamps, timestamps[1:])):
        raise ValueError("backtest requires a complete one-minute bar sequence without gaps")
    if require_complete and timestamps[-1] != closed - timedelta(minutes=1):
        raise ValueError("backtest requires the final regular-session one-minute bar")


def _ema(values: list[Decimal], period: int) -> Decimal:
    alpha = Decimal(2) / Decimal(period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * alpha + result * (Decimal(1) - alpha)
    return result


def _atr(bars: list[MinuteBar], period: int) -> Decimal:
    ranges: list[Decimal] = []
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1].close if index else bar.close
        ranges.append(max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close)))
    return sum(ranges[-period:], Decimal(0)) / Decimal(min(period, len(ranges)))


def _vwap(bars: list[MinuteBar]) -> Decimal:
    value_volume = sum(((bar.high + bar.low + bar.close) / Decimal(3)) * bar.volume for bar in bars)
    volume = sum(bar.volume for bar in bars)
    return value_volume / Decimal(volume) if volume else bars[-1].close


def _cost(price: Decimal, quantity: int, bps: Decimal) -> Decimal:
    return price * quantity * bps / Decimal(10_000)


def orb_vwap_entry_candidate(
    bars: list[MinuteBar], config: QuantConfig = QuantConfig(), *, market: Market,
) -> bool:
    """Evaluate a long entry from a complete prefix ending at the current bar."""
    _validate_session(bars, market)
    required = max(config.opening_range_bars, config.ema_period, config.atr_period,
                   config.relative_volume_period + 1)
    if len(bars) < required:
        return False
    opening_high = max(bar.high for bar in bars[:config.opening_range_bars])
    current = bars[-1]
    prior_volumes = [bar.volume for bar in bars[max(0, len(bars) - config.relative_volume_period - 1):-1]]
    average_volume = Decimal(sum(prior_volumes)) / len(prior_volumes) if prior_volumes else Decimal(0)
    relative_volume = Decimal(current.volume) / average_volume if average_volume else Decimal(0)
    spread_ok = current.spread_bps is not None and current.spread_bps <= config.maximum_spread_bps
    turnover_ok = current.close * current.volume >= config.minimum_turnover
    return (
        current.close > opening_high and current.close > _vwap(bars)
        and current.close > _ema([bar.close for bar in bars[-config.ema_period:]], config.ema_period)
        and relative_volume >= config.minimum_relative_volume and spread_ok and turnover_ok
    )


def backtest_orb_vwap(
    market: Market,
    symbol: str,
    bars: list[MinuteBar],
    config: QuantConfig = QuantConfig(),
    *,
    quantity: int = 1,
    entry_filter: Callable[[list[MinuteBar]], bool] | None = None,
) -> BacktestResult:
    """Long-only ORB/VWAP/EMA strategy. Signals use data available at each bar close;
    entries execute no earlier than the next bar open. If stop and target touch in one
    bar, stop is assumed to fill first. Open positions close at the final bar close.
    """
    _validate_session(bars, market, require_complete=True)
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol or quantity <= 0:
        raise ValueError("symbol and positive quantity are required")
    opening = bars[: config.opening_range_bars]
    if len(opening) < config.opening_range_bars:
        return BacktestResult(market, normalized_symbol, exchange_session_date(bars[0].timestamp, market), (), Decimal(0), Decimal(0), Decimal(0), len(bars), 0)
    opening_high = max(bar.high for bar in opening)
    minimum_history = max(config.ema_period, config.atr_period,
                          config.relative_volume_period + 1, config.opening_range_bars)
    trades: list[Trade] = []
    entry: tuple[int, Decimal, Decimal, Decimal] | None = None  # index, fill, stop, target
    signals = 0
    gross_total = Decimal(0)
    cost_total = Decimal(0)

    for index in range(config.opening_range_bars, len(bars)):
        bar = bars[index]
        prior = bars[:index]
        history = bars[: index + 1]
        if entry is not None:
            entry_index, fill_price, stop_price, target_price = entry
            exit_price: Decimal | None = None
            reason = ""
            if bar.open <= stop_price or bar.low <= stop_price:
                exit_price = min(bar.open, stop_price)  # gap-through stop is filled at the worse open
                reason = "ATR_STOP"
            elif bar.open >= target_price:
                exit_price, reason = bar.open, "TAKE_PROFIT_GAP"
            elif bar.high >= target_price:
                exit_price, reason = target_price, "TAKE_PROFIT"
            elif bar.close < _vwap(history):
                exit_price, reason = bar.close, "VWAP_LOSS"
            elif index == len(bars) - 1:
                exit_price, reason = bar.close, "SESSION_END"
            if exit_price is not None:
                # Use the observed full spread when available. If the exit bar lacks
                # a quote, charge the configured entry spread ceiling conservatively.
                exit_spread_bps = bar.spread_bps if bar.spread_bps is not None else config.maximum_spread_bps
                exit_cost_bps = config.slippage_bps + exit_spread_bps / Decimal(2)
                slipped_exit = exit_price * (Decimal(1) - exit_cost_bps / Decimal(10_000))
                raw_entry = bars[entry_index].open
                gross = (exit_price - raw_entry) * quantity
                execution_costs = ((fill_price - raw_entry) + (exit_price - slipped_exit)) * quantity
                commission_costs = (
                    _cost(fill_price, quantity, config.commission_bps)
                    + _cost(slipped_exit, quantity, config.commission_bps)
                )
                costs = execution_costs + commission_costs
                trades.append(Trade(bars[entry_index].timestamp, bar.timestamp, fill_price, slipped_exit, quantity, gross, costs, gross - costs, reason))
                gross_total += gross
                cost_total += costs
                entry = None
                continue

        # Each indicator is computed only from bars through this close.
        ready = index + 1 >= minimum_history
        quant_candidate = entry is None and ready and orb_vwap_entry_candidate(history, config, market=market)
        buy = quant_candidate and (entry_filter is None or entry_filter(history))
        if buy:
            signals += 1
            if index + 1 < len(bars):
                next_bar = bars[index + 1]
                if next_bar.spread_bps is not None and next_bar.spread_bps <= config.maximum_spread_bps:
                    entry_cost_bps = config.slippage_bps + next_bar.spread_bps / Decimal(2)
                    fill = next_bar.open * (Decimal(1) + entry_cost_bps / Decimal(10_000))
                    risk = _atr(history, config.atr_period) * config.atr_stop_multiple
                    stop = fill - risk
                    target = fill + risk * config.reward_risk_multiple
                    entry = (index + 1, fill, stop, target)

    return BacktestResult(
        market=market,
        symbol=normalized_symbol,
        session=exchange_session_date(bars[0].timestamp, market),
        trades=tuple(trades),
        gross_pnl=gross_total,
        costs=cost_total,
        net_pnl=gross_total - cost_total,
        data_points=len(bars),
        signals=signals,
    )
