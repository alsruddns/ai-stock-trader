"""Paper-session calendar and fail-closed entry gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from typing import Literal

from .quant import MinuteBar
from .market_calendar import exchange_session_window, korean_calendar_compatibility

Market = Literal["KR", "US"]


@dataclass(frozen=True)
class PaperOpsLimits:
    max_quote_age_seconds: int = 3
    max_signal_bar_age_seconds: int = 120
    max_daily_loss_pct: Decimal = Decimal("1.00")
    max_position_equity_pct: Decimal = Decimal("10.00")
    trade_risk_equity_pct: Decimal = Decimal("0.25")
    atr_stop_multiple: Decimal = Decimal("1.5")
    max_open_positions: int = 3
    max_consecutive_losses: int = 3
    flatten_before_close_minutes: int = 5

    @classmethod
    def from_environment(cls) -> "PaperOpsLimits":
        """Load local paper risk limits; malformed values fail closed at startup/use."""
        try:
            return cls(
                max_quote_age_seconds=int(os.getenv("PAPER_MAX_QUOTE_AGE_SECONDS", "3")),
                max_signal_bar_age_seconds=int(os.getenv("PAPER_MAX_SIGNAL_BAR_AGE_SECONDS", "120")),
                max_daily_loss_pct=Decimal(os.getenv("PAPER_DAILY_LOSS_LIMIT_PCT", "1.00")),
                max_position_equity_pct=Decimal(os.getenv("PAPER_MAX_POSITION_EQUITY_PCT", "10.00")),
                trade_risk_equity_pct=Decimal(os.getenv("PAPER_TRADE_RISK_EQUITY_PCT", "0.25")),
                atr_stop_multiple=Decimal(os.getenv("PAPER_ATR_STOP_MULTIPLE", "1.5")),
                max_open_positions=int(os.getenv("PAPER_MAX_OPEN_POSITIONS", "3")),
                max_consecutive_losses=int(os.getenv("PAPER_MAX_CONSECUTIVE_LOSSES", "3")),
                flatten_before_close_minutes=int(os.getenv("PAPER_FLATTEN_BEFORE_CLOSE_MINUTES", "5")),
            )
        except (ValueError, ArithmeticError) as exc:
            raise ValueError("paper operation risk settings are invalid") from exc

    def __post_init__(self) -> None:
        if min(self.max_quote_age_seconds, self.max_signal_bar_age_seconds,
               self.max_open_positions, self.max_consecutive_losses) <= 0:
            raise ValueError("paper operation limits must be positive")
        if (self.max_daily_loss_pct <= 0 or self.max_daily_loss_pct > Decimal("1.00")
                or self.max_position_equity_pct <= 0 or self.max_position_equity_pct > Decimal("10.00")
                or self.trade_risk_equity_pct <= 0 or self.trade_risk_equity_pct > Decimal("0.25")
                or self.max_open_positions > 3 or self.max_consecutive_losses > 3
                or self.atr_stop_multiple <= 0
                or self.flatten_before_close_minutes < 0):
            raise ValueError("paper operation limits cannot exceed the approved initial trading limits")


@dataclass(frozen=True)
class PaperOrderSizing:
    quantity: int
    notional: Decimal
    stop_distance: Decimal
    risk_budget: Decimal


@dataclass(frozen=True)
class PaperOrderSizingResult:
    sizing: PaperOrderSizing | None
    limiting_constraints: tuple[str, ...] = ()
    input_error: str | None = None

    @property
    def rejection_reason(self) -> str | None:
        if self.sizing is not None:
            return None
        if self.input_error is not None:
            return self.input_error
        if not self.limiting_constraints:
            return "PAPER_ORDER_SIZE_ZERO"
        if len(self.limiting_constraints) == 1:
            return f"{self.limiting_constraints[0]}_LIMIT_BELOW_ONE_SHARE"
        return f"{'_AND_'.join(self.limiting_constraints)}_LIMITS_BELOW_ONE_SHARE"


@dataclass(frozen=True)
class PaperExitCandidate:
    price: Decimal
    reason: str


def evaluate_paper_exit(
    bar: MinuteBar, *, stop_price: Decimal, target_price: Decimal, flatten_required: bool = False,
) -> PaperExitCandidate | None:
    """Evaluate stop-first exits on a completed bar; gap-through fills use the worse open."""
    if not (Decimal(0) < stop_price < target_price):
        raise ValueError("paper exit stop and target levels are invalid")
    if bar.open <= stop_price or bar.low <= stop_price:
        return PaperExitCandidate(min(bar.open, stop_price), "ATR_STOP")
    if bar.open >= target_price:
        return PaperExitCandidate(bar.open, "TAKE_PROFIT_GAP")
    if bar.high >= target_price:
        return PaperExitCandidate(target_price, "TAKE_PROFIT")
    if flatten_required:
        return PaperExitCandidate(bar.close, "SESSION_END")
    return None


def size_paper_order(
    *, price: Decimal, atr: Decimal, equity: Decimal, available_cash: Decimal,
    max_order_notional: Decimal, limits: PaperOpsLimits = PaperOpsLimits(),
) -> PaperOrderSizing | None:
    """Size a long-only paper entry under risk, concentration, cash, and notional caps."""
    return size_paper_order_diagnostic(
        price=price, atr=atr, equity=equity, available_cash=available_cash,
        max_order_notional=max_order_notional, limits=limits,
    ).sizing


def size_paper_order_diagnostic(
    *, price: Decimal, atr: Decimal, equity: Decimal, available_cash: Decimal,
    max_order_notional: Decimal, limits: PaperOpsLimits = PaperOpsLimits(),
) -> PaperOrderSizingResult:
    """Size a paper order and expose only which approved cap prevented a one-share entry."""
    inputs = (price, atr, equity, available_cash, max_order_notional)
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in inputs):
        return PaperOrderSizingResult(None, input_error="INVALID_SIZING_INPUT")
    if min(inputs) <= 0:
        return PaperOrderSizingResult(None, input_error="NON_POSITIVE_SIZING_INPUT")
    risk_budget = equity * limits.trade_risk_equity_pct / Decimal(100)
    stop_distance = atr * limits.atr_stop_multiple
    caps = {
        "TRADE_RISK": int(risk_budget / stop_distance),
        "POSITION_ALLOCATION": int(equity * limits.max_position_equity_pct / Decimal(100) / price),
        "SHARED_CASH": int(available_cash / price),
        "ORDER_NOTIONAL": int(max_order_notional / price),
    }
    limiting = tuple(name for name, quantity_limit in caps.items() if quantity_limit <= 0)
    if limiting:
        return PaperOrderSizingResult(None, limiting)
    quantity = min(caps.values())
    return PaperOrderSizingResult(
        PaperOrderSizing(quantity, price * quantity, stop_distance, risk_budget),
    )


@dataclass(frozen=True)
class PaperGateState:
    market: Market
    session_open: bool
    flatten_required: bool
    new_entries_allowed: bool
    exit_management_allowed: bool
    reasons: tuple[str, ...]


def evaluate_paper_gate(
    market: Market,
    now: datetime,
    latest_quote_at: datetime | None,
    *,
    data_feed_healthy: bool,
    model_available: bool,
    model_approved: bool,
    broker_healthy: bool,
    daily_loss_pct: Decimal,
    consecutive_losses: int,
    open_positions: int,
    limits: PaperOpsLimits = PaperOpsLimits(),
) -> PaperGateState:
    """Block new entries on stale/failed dependencies or risk-limit breaches.

    Existing positions may continue to receive exit management even when new entries
    are blocked. Callers must not interpret this function as an order authorization.
    """
    if now.tzinfo is None:
        raise ValueError("current time must include a timezone")
    reasons: list[str] = []
    calendar_unavailable = False
    try:
        window = exchange_session_window(market, now)
    except RuntimeError:
        window = None
        calendar_unavailable = True
        reasons.append("MARKET_CALENDAR_UNAVAILABLE")
    open_now = window is not None and window[0] <= now.astimezone(timezone.utc) < window[1]
    if not open_now and not calendar_unavailable:
        reasons.append("MARKET_CLOSED")
    flatten = bool(window and window[1] - timedelta(minutes=limits.flatten_before_close_minutes) <= now.astimezone(timezone.utc) < window[1])
    if latest_quote_at is None:
        reasons.append("QUOTE_MISSING")
    elif latest_quote_at.tzinfo is None:
        raise ValueError("quote timestamp must include a timezone")
    else:
        quote_age_seconds = (now.astimezone(timezone.utc) - latest_quote_at.astimezone(timezone.utc)).total_seconds()
        if quote_age_seconds < -1:
            reasons.append("QUOTE_TIMESTAMP_IN_FUTURE")
        elif quote_age_seconds > limits.max_quote_age_seconds:
            reasons.append("QUOTE_STALE")
    if not data_feed_healthy:
        reasons.append("DATA_FEED_UNHEALTHY")
    if not model_available:
        reasons.append("MODEL_UNAVAILABLE")
    if not model_approved:
        reasons.append("MODEL_NOT_APPROVED")
    if not broker_healthy:
        reasons.append("PAPER_BROKER_UNHEALTHY")
    if not isinstance(daily_loss_pct, Decimal) or not daily_loss_pct.is_finite():
        reasons.append("RISK_INPUT_INVALID")
    elif daily_loss_pct >= limits.max_daily_loss_pct:
        reasons.append("DAILY_LOSS_LIMIT")
    if (not isinstance(consecutive_losses, int) or isinstance(consecutive_losses, bool)
            or consecutive_losses < 0):
        reasons.append("RISK_INPUT_INVALID")
    elif consecutive_losses >= limits.max_consecutive_losses:
        reasons.append("CONSECUTIVE_LOSS_LIMIT")
    if (not isinstance(open_positions, int) or isinstance(open_positions, bool) or open_positions < 0):
        reasons.append("RISK_INPUT_INVALID")
    elif open_positions >= limits.max_open_positions:
        reasons.append("POSITION_LIMIT")
    if flatten:
        reasons.append("SESSION_ENDING")
    return PaperGateState(
        market=market,
        session_open=open_now,
        flatten_required=flatten,
        new_entries_allowed=open_now and not reasons,
        exit_management_allowed=open_now,
        reasons=tuple(dict.fromkeys(reasons)),
    )
