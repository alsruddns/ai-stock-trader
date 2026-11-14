"""Domestic-only portfolio strategy replay. The result can never authorize orders."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Callable

from .quant import (
    MinuteBar, QuantConfig, _atr, _cost, _validate_session, _vwap,
    exchange_session_date, orb_vwap_entry_candidate, risk_bounded_quantity_bounds,
)

_PORTFOLIO_TOTAL_TOLERANCE = Decimal("1e-18")


@dataclass(frozen=True)
class PortfolioTrade:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    quantity: int
    entry_notional: Decimal
    gross_pnl: Decimal
    costs: Decimal
    entry_slippage_cost: Decimal
    entry_spread_cost: Decimal
    exit_slippage_cost: Decimal
    exit_spread_cost: Decimal
    buy_commission_cost: Decimal
    sell_commission_cost: Decimal
    sell_tax_cost: Decimal
    net_pnl: Decimal
    exit_reason: str

    @property
    def costs_by_component(self) -> dict[str, Decimal]:
        return {
            "entry_slippage": self.entry_slippage_cost,
            "entry_spread": self.entry_spread_cost,
            "exit_slippage": self.exit_slippage_cost,
            "exit_spread": self.exit_spread_cost,
            "buy_commission_and_exchange_fee": self.buy_commission_cost,
            "sell_commission_and_exchange_fee": self.sell_commission_cost,
            "sell_tax": self.sell_tax_cost,
        }


@dataclass(frozen=True)
class PortfolioBacktestResult:
    market: str
    session: date
    starting_equity: Decimal
    ending_equity: Decimal
    gross_pnl: Decimal
    estimated_costs: Decimal
    estimated_costs_by_component: dict[str, Decimal]
    net_pnl: Decimal
    trades: tuple[PortfolioTrade, ...]
    quant_signals_by_symbol: dict[str, int]
    signals_by_symbol: dict[str, int]
    ai_rejections_by_symbol: dict[str, int]
    rejected_entries: tuple[tuple[str, str], ...]
    maximum_open_positions: int
    daily_loss_limit_hit: bool
    consecutive_loss_limit_hit: bool
    ending_consecutive_losses: int
    max_bar_marked_drawdown_amount: Decimal
    max_bar_marked_drawdown_percent: Decimal
    minimum_bar_marked_equity: Decimal
    maximum_bar_marked_equity: Decimal
    bar_marked_equity_observations: int
    bar_marked_equity_basis: str
    executable: bool = False


@dataclass(frozen=True)
class CompoundedDrawdown:
    maximum_amount: Decimal
    maximum_percent: Decimal
    observations: int


@dataclass(frozen=True)
class SlippageSensitivityResult:
    slippage_bps_per_fill: Decimal
    session_count: int
    accepted_trade_count: int
    gross_pnl: Decimal
    estimated_costs: Decimal
    net_pnl: Decimal
    ending_equity: Decimal | None
    maximum_drawdown_amount: Decimal
    maximum_drawdown_percent: Decimal
    drawdown_basis: str
    executable: bool = False


@dataclass(frozen=True)
class CapitalSensitivityResult:
    starting_equity: Decimal
    max_order_notional: Decimal
    session_count: int
    accepted_trade_count: int
    gross_pnl: Decimal
    estimated_costs: Decimal
    net_pnl: Decimal
    ending_equity: Decimal | None
    maximum_drawdown_amount: Decimal
    maximum_drawdown_percent: Decimal
    drawdown_basis: str
    trade_outcomes_by_symbol: dict[str, dict[str, int | Decimal]]
    rejected_entries_by_reason: dict[str, int]
    rejected_entries_by_symbol_and_reason: dict[str, dict[str, int]]
    executable: bool = False


def analyze_slippage_sensitivity(
    sessions_by_date: dict[date, dict[str, list[MinuteBar]]], *,
    starting_equity: Decimal, max_order_notional: Decimal, config: QuantConfig,
    slippage_scenarios_bps: list[Decimal], carry_equity_between_sessions: bool = False,
    entry_filter: Callable[[str, list[MinuteBar]], bool] | None = None,
) -> tuple[SlippageSensitivityResult, ...]:
    """Replay identical domestic sessions under alternative per-fill slippage assumptions."""
    if (not isinstance(starting_equity, Decimal) or not starting_equity.is_finite()
            or starting_equity <= 0):
        raise ValueError("starting equity must be a positive finite decimal")
    if (not isinstance(max_order_notional, Decimal) or not max_order_notional.is_finite()
            or max_order_notional <= 0):
        raise ValueError("maximum order notional must be a positive finite decimal")
    if not isinstance(sessions_by_date, dict) or not sessions_by_date:
        raise ValueError("slippage sensitivity requires domestic session data")
    if not isinstance(slippage_scenarios_bps, list) or not slippage_scenarios_bps:
        raise ValueError("at least one slippage scenario is required")
    scenarios: list[Decimal] = []
    for value in slippage_scenarios_bps:
        if (not isinstance(value, Decimal) or not value.is_finite()
                or value < 0 or value > Decimal("10000")):
            raise ValueError("slippage scenarios must be finite values between zero and 10000 bps")
        if value in scenarios:
            raise ValueError("slippage scenarios must be unique")
        scenarios.append(value)
    if not isinstance(config, QuantConfig):
        raise ValueError("slippage sensitivity requires a quant cost configuration")

    session_dates = list(sessions_by_date)
    if any(not isinstance(session, date) or isinstance(session, datetime) for session in session_dates):
        raise ValueError("slippage sensitivity dates must be domestic session dates")
    if not isinstance(carry_equity_between_sessions, bool):
        raise ValueError("carry-equity mode must be a boolean")
    ordered_sessions = sorted(session_dates)
    results: list[SlippageSensitivityResult] = []
    for slippage in scenarios:
        scenario_config = replace(config, slippage_bps=slippage)
        session_results: list[PortfolioBacktestResult] = []
        equity = starting_equity
        consecutive_losses = 0
        for session in ordered_sessions:
            session_result = backtest_domestic_portfolio(
                sessions_by_date[session],
                starting_equity=equity if carry_equity_between_sessions else starting_equity,
                max_order_notional=max_order_notional,
                config=scenario_config,
                initial_consecutive_losses=consecutive_losses if carry_equity_between_sessions else 0,
                entry_filter=entry_filter,
            )
            session_results.append(session_result)
            if carry_equity_between_sessions:
                equity = session_result.ending_equity
                consecutive_losses = session_result.ending_consecutive_losses

        gross_pnl = sum((item.gross_pnl for item in session_results), Decimal(0))
        estimated_costs = sum((item.estimated_costs for item in session_results), Decimal(0))
        net_pnl = sum((item.net_pnl for item in session_results), Decimal(0))
        if abs(gross_pnl - estimated_costs - net_pnl) > _PORTFOLIO_TOTAL_TOLERANCE:
            raise ValueError("slippage sensitivity totals do not reconcile")
        if carry_equity_between_sessions:
            drawdown = aggregate_compounded_drawdown(starting_equity, session_results)
            drawdown_amount = drawdown.maximum_amount
            drawdown_percent = drawdown.maximum_percent
            drawdown_basis = "compounded_sequential_sessions"
            ending_equity: Decimal | None = equity
        else:
            drawdown_amount = max((item.max_bar_marked_drawdown_amount for item in session_results), default=Decimal(0))
            drawdown_percent = max((item.max_bar_marked_drawdown_percent for item in session_results), default=Decimal(0))
            drawdown_basis = "maximum_independent_session"
            ending_equity = None
        results.append(SlippageSensitivityResult(
            slippage_bps_per_fill=slippage,
            session_count=len(session_results),
            accepted_trade_count=sum(len(item.trades) for item in session_results),
            gross_pnl=gross_pnl,
            estimated_costs=estimated_costs,
            net_pnl=net_pnl,
            ending_equity=ending_equity,
            maximum_drawdown_amount=drawdown_amount,
            maximum_drawdown_percent=drawdown_percent,
            drawdown_basis=drawdown_basis,
        ))
    return tuple(results)


def analyze_capital_sensitivity(
    sessions_by_date: dict[date, dict[str, list[MinuteBar]]], *,
    starting_equity: Decimal, max_order_notional: Decimal, config: QuantConfig,
    capital_scenarios: list[Decimal], carry_equity_between_sessions: bool = False,
    entry_filter: Callable[[str, list[MinuteBar]], bool] | None = None,
) -> tuple[CapitalSensitivityResult, ...]:
    """Replay identical sessions at scaled capital while preserving percentage risk caps.

    The simulated order-notional ceiling scales by the same factor as starting
    equity. This isolates whole-share and shared-capital effects without changing
    the approved percentage-based trade-risk or position-allocation limits.
    """
    if (not isinstance(starting_equity, Decimal) or not starting_equity.is_finite()
            or starting_equity <= 0):
        raise ValueError("starting equity must be a positive finite decimal")
    if (not isinstance(max_order_notional, Decimal) or not max_order_notional.is_finite()
            or max_order_notional <= 0):
        raise ValueError("maximum order notional must be a positive finite decimal")
    if not isinstance(sessions_by_date, dict) or not sessions_by_date:
        raise ValueError("capital sensitivity requires domestic session data")
    if not isinstance(capital_scenarios, list) or not capital_scenarios:
        raise ValueError("at least one capital scenario is required")
    scenarios: list[Decimal] = []
    for value in capital_scenarios:
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise ValueError("capital scenarios must be positive finite decimals")
        if value in scenarios:
            raise ValueError("capital scenarios must be unique")
        scenarios.append(value)
    if not isinstance(config, QuantConfig):
        raise ValueError("capital sensitivity requires a quant cost configuration")
    session_dates = list(sessions_by_date)
    if any(not isinstance(session, date) or isinstance(session, datetime) for session in session_dates):
        raise ValueError("capital sensitivity dates must be domestic session dates")
    if not isinstance(carry_equity_between_sessions, bool):
        raise ValueError("carry-equity mode must be a boolean")

    ordered_sessions = sorted(session_dates)
    results: list[CapitalSensitivityResult] = []
    for scenario_equity in scenarios:
        scale = scenario_equity / starting_equity
        scenario_order_notional = max_order_notional * scale
        session_results: list[PortfolioBacktestResult] = []
        equity = scenario_equity
        consecutive_losses = 0
        for session in ordered_sessions:
            session_result = backtest_domestic_portfolio(
                sessions_by_date[session],
                starting_equity=equity if carry_equity_between_sessions else scenario_equity,
                max_order_notional=scenario_order_notional,
                config=config,
                initial_consecutive_losses=consecutive_losses if carry_equity_between_sessions else 0,
                entry_filter=entry_filter,
            )
            session_results.append(session_result)
            if carry_equity_between_sessions:
                equity = session_result.ending_equity
                consecutive_losses = session_result.ending_consecutive_losses

        gross_pnl = sum((item.gross_pnl for item in session_results), Decimal(0))
        estimated_costs = sum((item.estimated_costs for item in session_results), Decimal(0))
        net_pnl = sum((item.net_pnl for item in session_results), Decimal(0))
        if abs(gross_pnl - estimated_costs - net_pnl) > _PORTFOLIO_TOTAL_TOLERANCE:
            raise ValueError("capital sensitivity totals do not reconcile")
        rejected: dict[str, int] = {}
        rejected_by_symbol: dict[str, dict[str, int]] = {}
        trade_outcomes_by_symbol: dict[str, dict[str, int | Decimal]] = {
            symbol: {
                "trade_count": 0,
                "winning_trade_count": 0,
                "losing_trade_count": 0,
                "gross_pnl": Decimal(0),
                "estimated_costs": Decimal(0),
                "net_pnl": Decimal(0),
            }
            for symbol in sorted({symbol for symbols in sessions_by_date.values() for symbol in symbols})
        }
        for item in session_results:
            for symbol, reason in item.rejected_entries:
                rejected[reason] = rejected.get(reason, 0) + 1
                symbol_rejections = rejected_by_symbol.setdefault(symbol, {})
                symbol_rejections[reason] = symbol_rejections.get(reason, 0) + 1
            for trade in item.trades:
                outcome = trade_outcomes_by_symbol.setdefault(trade.symbol, {
                    "trade_count": 0,
                    "winning_trade_count": 0,
                    "losing_trade_count": 0,
                    "gross_pnl": Decimal(0),
                    "estimated_costs": Decimal(0),
                    "net_pnl": Decimal(0),
                })
                outcome["trade_count"] = int(outcome["trade_count"]) + 1
                outcome["winning_trade_count"] = int(outcome["winning_trade_count"]) + int(trade.net_pnl > 0)
                outcome["losing_trade_count"] = int(outcome["losing_trade_count"]) + int(trade.net_pnl < 0)
                outcome["gross_pnl"] = Decimal(outcome["gross_pnl"]) + trade.gross_pnl
                outcome["estimated_costs"] = Decimal(outcome["estimated_costs"]) + trade.costs
                outcome["net_pnl"] = Decimal(outcome["net_pnl"]) + trade.net_pnl
        for symbol, outcome in trade_outcomes_by_symbol.items():
            if abs(Decimal(outcome["gross_pnl"]) - Decimal(outcome["estimated_costs"])
                   - Decimal(outcome["net_pnl"])) > _PORTFOLIO_TOTAL_TOLERANCE:
                raise ValueError(f"capital sensitivity trade outcomes do not reconcile for {symbol}")
        if carry_equity_between_sessions:
            drawdown = aggregate_compounded_drawdown(scenario_equity, session_results)
            drawdown_amount = drawdown.maximum_amount
            drawdown_percent = drawdown.maximum_percent
            drawdown_basis = "compounded_sequential_sessions"
            ending_equity: Decimal | None = equity
        else:
            drawdown_amount = max(
                (item.max_bar_marked_drawdown_amount for item in session_results), default=Decimal(0),
            )
            drawdown_percent = max(
                (item.max_bar_marked_drawdown_percent for item in session_results), default=Decimal(0),
            )
            drawdown_basis = "maximum_independent_session"
            ending_equity = None
        results.append(CapitalSensitivityResult(
            starting_equity=scenario_equity,
            max_order_notional=scenario_order_notional,
            session_count=len(session_results),
            accepted_trade_count=sum(len(item.trades) for item in session_results),
            gross_pnl=gross_pnl,
            estimated_costs=estimated_costs,
            net_pnl=net_pnl,
            ending_equity=ending_equity,
            maximum_drawdown_amount=drawdown_amount,
            maximum_drawdown_percent=drawdown_percent,
            drawdown_basis=drawdown_basis,
            trade_outcomes_by_symbol=trade_outcomes_by_symbol,
            rejected_entries_by_reason=dict(sorted(rejected.items())),
            rejected_entries_by_symbol_and_reason={
                symbol: dict(sorted(reasons.items()))
                for symbol, reasons in sorted(rejected_by_symbol.items())
            },
        ))
    return tuple(results)


def aggregate_compounded_drawdown(
    starting_equity: Decimal, sessions: list[PortfolioBacktestResult],
) -> CompoundedDrawdown:
    """Combine per-session marks into a sequential high-water drawdown."""
    if not isinstance(starting_equity, Decimal) or not starting_equity.is_finite() or starting_equity <= 0:
        raise ValueError("starting equity must be a positive finite decimal")
    if not isinstance(sessions, list) or not sessions:
        raise ValueError("compounded drawdown requires at least one ordered session")
    high_water = starting_equity
    maximum_amount = Decimal(0)
    maximum_percent = Decimal(0)
    observations = 0
    previous_date: date | None = None
    previous_ending_equity: Decimal | None = None
    for index, session in enumerate(sessions):
        if not isinstance(session, PortfolioBacktestResult):
            raise ValueError("compounded drawdown sessions must be portfolio results")
        if (not isinstance(session.session, date) or isinstance(session.session, datetime)
                or session.market != "KR"):
            raise ValueError("compounded drawdown requires domestic session dates")
        if previous_date is not None and session.session <= previous_date:
            raise ValueError("compounded drawdown sessions must be strictly chronological")
        if (not isinstance(session.starting_equity, Decimal) or not session.starting_equity.is_finite()
                or session.starting_equity <= 0
                or not isinstance(session.ending_equity, Decimal) or not session.ending_equity.is_finite()
                or session.ending_equity < 0
                or not isinstance(session.gross_pnl, Decimal) or not session.gross_pnl.is_finite()
                or not isinstance(session.estimated_costs, Decimal) or not session.estimated_costs.is_finite()
                or session.estimated_costs < 0
                or not isinstance(session.net_pnl, Decimal) or not session.net_pnl.is_finite()):
            raise ValueError("session equity is invalid for compounded drawdown")
        if (abs(session.gross_pnl - session.estimated_costs - session.net_pnl)
                > _PORTFOLIO_TOTAL_TOLERANCE
                or abs(session.starting_equity + session.net_pnl - session.ending_equity)
                > _PORTFOLIO_TOTAL_TOLERANCE):
            raise ValueError("session PnL does not reconcile to compounded equity")
        expected_starting_equity = starting_equity if index == 0 else previous_ending_equity
        if abs(session.starting_equity - expected_starting_equity) > _PORTFOLIO_TOTAL_TOLERANCE:
            raise ValueError("compounded drawdown session equity is not sequential")
        minimum = session.minimum_bar_marked_equity
        maximum = session.maximum_bar_marked_equity
        session_drawdown = session.max_bar_marked_drawdown_amount
        session_drawdown_percent = session.max_bar_marked_drawdown_percent
        session_observations = session.bar_marked_equity_observations
        if (not isinstance(minimum, Decimal) or not minimum.is_finite()
                or not isinstance(maximum, Decimal) or not maximum.is_finite()
                or minimum < 0 or maximum < minimum
                or maximum < session.starting_equity
                or not isinstance(session_drawdown, Decimal) or not session_drawdown.is_finite()
                or session_drawdown < 0
                or not isinstance(session_drawdown_percent, Decimal) or not session_drawdown_percent.is_finite()
                or session_drawdown_percent < 0
                or not isinstance(session_observations, int) or isinstance(session_observations, bool)
                or session_observations < 1):
            raise ValueError("session equity marks are invalid for compounded drawdown")
        from_prior_high = max(Decimal(0), high_water - minimum)
        from_prior_high_percent = from_prior_high * Decimal(100) / high_water
        maximum_amount = max(maximum_amount, from_prior_high, session_drawdown)
        maximum_percent = max(maximum_percent, from_prior_high_percent,
                              session_drawdown_percent)
        high_water = max(high_water, maximum)
        observations += session_observations
        previous_date = session.session
        previous_ending_equity = session.ending_equity
    return CompoundedDrawdown(maximum_amount, maximum_percent, observations)


@dataclass(frozen=True)
class _PendingEntry:
    stop_distance: Decimal
    signal_spread_bps: Decimal
    signal_turnover: Decimal


@dataclass
class _OpenPosition:
    entry_index: int
    raw_entry_price: Decimal
    fill_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    quantity: int
    cash_debit: Decimal
    entry_spread_bps: Decimal


def backtest_domestic_portfolio(
    bars_by_symbol: dict[str, list[MinuteBar]], *, starting_equity: Decimal,
    max_order_notional: Decimal, config: QuantConfig = QuantConfig(),
    max_open_positions: int = 3, max_daily_loss_pct: Decimal = Decimal("1"),
    max_consecutive_losses: int = 3, initial_consecutive_losses: int = 0,
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
    if (not isinstance(initial_consecutive_losses, int)
            or isinstance(initial_consecutive_losses, bool)
            or not 0 <= initial_consecutive_losses <= max_consecutive_losses):
        raise ValueError("initial consecutive losses must be between zero and the configured limit")
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
    loss_streak = initial_consecutive_losses
    daily_loss_hit = False
    streak_hit = loss_streak >= max_consecutive_losses
    maximum_open = 0
    daily_loss_budget = starting_equity * max_daily_loss_pct / Decimal(100)
    high_water_equity = starting_equity
    max_drawdown_amount = Decimal(0)
    max_drawdown_percent = Decimal(0)
    minimum_equity = starting_equity
    maximum_equity = starting_equity
    equity_observations = 0

    def marked_pnl(index: int, *, price_field: str) -> Decimal:
        value = Decimal(0)
        for symbol, position in positions.items():
            price = getattr(normalized_bars[symbol][index], price_field)
            liquidation_bps = (config.effective_sell_commission_bps + config.sell_tax_bps
                               + config.slippage_bps + config.maximum_spread_bps / Decimal(2))
            proceeds = price * (Decimal(1) - liquidation_bps / Decimal(10_000)) * position.quantity
            value += proceeds - position.cash_debit
        return value

    def observe_equity(equity: Decimal) -> None:
        nonlocal high_water_equity, max_drawdown_amount, max_drawdown_percent
        nonlocal minimum_equity, maximum_equity, equity_observations
        equity_observations += 1
        minimum_equity = min(minimum_equity, equity)
        maximum_equity = max(maximum_equity, equity)
        high_water_equity = max(high_water_equity, equity)
        drawdown = high_water_equity - equity
        drawdown_percent = drawdown * Decimal(100) / high_water_equity
        max_drawdown_amount = max(max_drawdown_amount, drawdown)
        max_drawdown_percent = max(max_drawdown_percent, drawdown_percent)

    for index, timestamp in enumerate(timestamps):
        # Check open-to-open gaps before placing new entries for this minute.
        observe_equity(starting_equity + realized + marked_pnl(index, price_field="open"))
        if realized + marked_pnl(index, price_field="open") <= -daily_loss_budget:
            daily_loss_hit = True

        # Resolve simultaneous candidates using the approved liquidity/cost
        # inputs, not ticker-code ordering. Ticker is only a stable final tie-break.
        pending_symbols = sorted(
            pending,
            key=lambda symbol: (
                -pending[symbol].signal_turnover,
                pending[symbol].signal_spread_bps,
                symbol,
            ),
        )
        for symbol in pending_symbols:
            intent = pending.pop(symbol)
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
            quantity_bounds = risk_bounded_quantity_bounds(
                entry_price=fill, stop_distance=intent.stop_distance, equity=equity_now,
                max_order_notional=max_order_notional,
                buy_commission_bps=config.effective_buy_commission_bps,
                sell_commission_bps=config.effective_sell_commission_bps,
                sell_tax_bps=config.sell_tax_bps, slippage_bps=config.slippage_bps,
                maximum_spread_bps=intent.signal_spread_bps,
            )
            debit_per_share = fill * (Decimal(1) + config.effective_buy_commission_bps / Decimal(10_000))
            quantity = quantity_bounds.permitted_quantity
            cash_quantity = max(0, int(cash / debit_per_share))
            if quantity <= 0:
                limiting_bounds = [
                    name for name, value in (
                        ("TRADE_RISK", quantity_bounds.risk),
                        ("POSITION_ALLOCATION", quantity_bounds.allocation),
                        ("ORDER_NOTIONAL", quantity_bounds.order_notional),
                    ) if value == 0
                ]
                suffix = "LIMIT_BELOW_ONE_SHARE" if len(limiting_bounds) == 1 else "LIMITS_BELOW_ONE_SHARE"
                reason = f"{'_AND_'.join(limiting_bounds)}_{suffix}"
                rejected.append((symbol, reason))
                continue
            quantity = min(quantity, cash_quantity)
            if quantity <= 0:
                rejected.append((symbol, "SHARED_CASH_BELOW_ONE_SHARE"))
                continue
            distance = intent.stop_distance
            debit = debit_per_share * quantity
            cash -= debit
            positions[symbol] = _OpenPosition(
                entry_index=index, raw_entry_price=bar.open, fill_price=fill,
                stop_price=fill - distance,
                target_price=fill + distance * config.reward_risk_multiple,
                quantity=quantity, cash_debit=debit, entry_spread_bps=intent.signal_spread_bps,
            )
            maximum_open = max(maximum_open, len(positions))

        # Use each completed bar's adverse low mark before applying stop/target exits.
        # This records intrabar downside excursions that an open/close-only curve misses.
        observe_equity(starting_equity + realized + marked_pnl(index, price_field="low"))

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
            entry_slippage_cost = _cost(position.raw_entry_price, quantity, config.slippage_bps)
            entry_spread_cost = _cost(position.raw_entry_price, quantity,
                                      position.entry_spread_bps / Decimal(2))
            exit_slippage_cost = _cost(exit_price, quantity, config.slippage_bps)
            exit_spread_cost = _cost(exit_price, quantity, observed_spread / Decimal(2))
            buy_commission_cost = _cost(position.fill_price, quantity, config.effective_buy_commission_bps)
            sell_commission_cost = _cost(slipped_exit, quantity, config.effective_sell_commission_bps)
            sell_tax_cost = _cost(slipped_exit, quantity, config.sell_tax_bps)
            costs = sum((entry_slippage_cost, entry_spread_cost, exit_slippage_cost,
                         exit_spread_cost, buy_commission_cost, sell_commission_cost, sell_tax_cost), Decimal(0))
            net_pnl = gross - costs
            completed.append(PortfolioTrade(
                symbol, timestamps[position.entry_index], timestamp, quantity,
                position.fill_price * quantity,
                gross, costs, entry_slippage_cost, entry_spread_cost, exit_slippage_cost,
                exit_spread_cost, buy_commission_cost, sell_commission_cost, sell_tax_cost,
                net_pnl, reason,
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
        observe_equity(starting_equity + realized + marked_pnl(index, price_field="close"))

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
                    signal_turnover=signal_bar.close * signal_bar.volume,
                )

    if positions:
        raise ValueError("all domestic portfolio positions must be closed by the session end")
    gross_pnl = sum((trade.gross_pnl for trade in completed), Decimal(0))
    estimated_costs = sum((trade.costs for trade in completed), Decimal(0))
    cost_component_names = (
        "entry_slippage", "entry_spread", "exit_slippage", "exit_spread",
        "buy_commission_and_exchange_fee", "sell_commission_and_exchange_fee", "sell_tax",
    )
    estimated_costs_by_component = {
        name: sum((trade.costs_by_component[name] for trade in completed), Decimal(0))
        for name in cost_component_names
    }
    trade_net_pnl = sum((trade.net_pnl for trade in completed), Decimal(0))
    if (abs(gross_pnl - estimated_costs - trade_net_pnl) > _PORTFOLIO_TOTAL_TOLERANCE
            or abs(trade_net_pnl - realized) > _PORTFOLIO_TOTAL_TOLERANCE
            or abs(sum(estimated_costs_by_component.values(), Decimal(0)) - estimated_costs)
            > _PORTFOLIO_TOTAL_TOLERANCE):
        raise ValueError("portfolio totals do not reconcile to completed trade results")
    return PortfolioBacktestResult(
        market="KR", session=session, starting_equity=starting_equity,
        ending_equity=starting_equity + realized, gross_pnl=gross_pnl,
        estimated_costs=estimated_costs, estimated_costs_by_component=estimated_costs_by_component,
        net_pnl=realized,
        trades=tuple(completed), quant_signals_by_symbol=quant_signals,
        signals_by_symbol=signals, ai_rejections_by_symbol=ai_rejections,
        rejected_entries=tuple(rejected), maximum_open_positions=maximum_open,
        daily_loss_limit_hit=daily_loss_hit, consecutive_loss_limit_hit=streak_hit,
        ending_consecutive_losses=loss_streak,
        max_bar_marked_drawdown_amount=max_drawdown_amount,
        max_bar_marked_drawdown_percent=max_drawdown_percent,
        minimum_bar_marked_equity=minimum_equity,
        maximum_bar_marked_equity=maximum_equity,
        bar_marked_equity_observations=equity_observations,
        bar_marked_equity_basis=(
            "high-water starts at session starting equity; each bar marks open, adverse low, and close; "
            "open positions use configured conservative exit costs"
        ),
        executable=False,
    )
