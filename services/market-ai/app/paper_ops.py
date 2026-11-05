"""Paper-session calendar and fail-closed entry gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

Market = Literal["KR", "US"]


@dataclass(frozen=True)
class PaperOpsLimits:
    max_quote_age_seconds: int = 3
    max_daily_loss_pct: Decimal = Decimal("1.00")
    max_position_equity_pct: Decimal = Decimal("10.00")
    trade_risk_equity_pct: Decimal = Decimal("0.25")
    atr_stop_multiple: Decimal = Decimal("1.5")
    max_open_positions: int = 3
    max_consecutive_losses: int = 3
    flatten_before_close_minutes: int = 5

    def __post_init__(self) -> None:
        if min(self.max_quote_age_seconds, self.max_open_positions, self.max_consecutive_losses) <= 0:
            raise ValueError("paper operation limits must be positive")
        if (self.max_daily_loss_pct <= 0 or self.max_position_equity_pct <= 0
                or self.trade_risk_equity_pct <= 0 or self.atr_stop_multiple <= 0
                or self.flatten_before_close_minutes < 0):
            raise ValueError("paper operation risk limits are invalid")


@dataclass(frozen=True)
class PaperOrderSizing:
    quantity: int
    notional: Decimal
    stop_distance: Decimal
    risk_budget: Decimal


def size_paper_order(
    *, price: Decimal, atr: Decimal, equity: Decimal, available_cash: Decimal,
    max_order_notional: Decimal, limits: PaperOpsLimits = PaperOpsLimits(),
) -> PaperOrderSizing | None:
    """Size a long-only paper entry under risk, concentration, cash, and notional caps."""
    if min(price, atr, equity, available_cash, max_order_notional) <= 0:
        return None
    risk_budget = equity * limits.trade_risk_equity_pct / Decimal(100)
    stop_distance = atr * limits.atr_stop_multiple
    caps = (
        risk_budget / stop_distance,
        equity * limits.max_position_equity_pct / Decimal(100) / price,
        available_cash / price,
        max_order_notional / price,
    )
    quantity = int(min(caps))
    if quantity <= 0:
        return None
    return PaperOrderSizing(quantity, price * quantity, stop_distance, risk_budget)


@dataclass(frozen=True)
class PaperGateState:
    market: Market
    session_open: bool
    flatten_required: bool
    new_entries_allowed: bool
    exit_management_allowed: bool
    reasons: tuple[str, ...]


def exchange_session_window(market: Market, now: datetime) -> tuple[datetime, datetime] | None:
    """Return the official exchange calendar open/close in UTC; unsupported dates fail closed."""
    if now.tzinfo is None:
        raise ValueError("current time must include a timezone")
    try:
        import exchange_calendars as calendars
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("Install the optional ops dependencies to use market sessions") from exc
    calendar_name = "XKRX" if market == "KR" else "XNYS" if market == "US" else None
    if calendar_name is None:
        raise ValueError("market must be KR or US")
    exchange_zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    label = pd.Timestamp(now.astimezone(exchange_zone).date())
    try:
        calendar = calendars.get_calendar(calendar_name, start=label - pd.Timedelta(days=10), end=label + pd.Timedelta(days=10))
        if not calendar.is_session(label):
            return None
        row = calendar.schedule.loc[label]
        opened = row["open"].to_pydatetime().astimezone(timezone.utc)
        closed = row["close"].to_pydatetime().astimezone(timezone.utc)
        return opened, closed
    except (KeyError, ValueError, IndexError) as exc:
        raise RuntimeError("market calendar could not verify this date") from exc


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
    window = exchange_session_window(market, now)
    open_now = window is not None and window[0] <= now.astimezone(timezone.utc) < window[1]
    if not open_now:
        reasons.append("MARKET_CLOSED")
    flatten = bool(window and window[1] - timedelta(minutes=limits.flatten_before_close_minutes) <= now.astimezone(timezone.utc) < window[1])
    if latest_quote_at is None:
        reasons.append("QUOTE_MISSING")
    elif latest_quote_at.tzinfo is None:
        raise ValueError("quote timestamp must include a timezone")
    elif (now.astimezone(timezone.utc) - latest_quote_at.astimezone(timezone.utc)).total_seconds() > limits.max_quote_age_seconds:
        reasons.append("QUOTE_STALE")
    if not data_feed_healthy:
        reasons.append("DATA_FEED_UNHEALTHY")
    if not model_available:
        reasons.append("MODEL_UNAVAILABLE")
    if not model_approved:
        reasons.append("MODEL_NOT_APPROVED")
    if not broker_healthy:
        reasons.append("PAPER_BROKER_UNHEALTHY")
    if daily_loss_pct >= limits.max_daily_loss_pct:
        reasons.append("DAILY_LOSS_LIMIT")
    if consecutive_losses >= limits.max_consecutive_losses:
        reasons.append("CONSECUTIVE_LOSS_LIMIT")
    if open_positions >= limits.max_open_positions:
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
