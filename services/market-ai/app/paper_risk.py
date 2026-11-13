"""Fail-closed valuation helpers for paper daily-loss controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping


@dataclass(frozen=True)
class EquitySnapshot:
    currency: str
    equity: Decimal | None
    reason: str | None


def paper_equity_snapshot(
    currency: str,
    *,
    cash_balances: list[dict],
    positions: list[dict],
    quotes: Mapping[tuple[str, str], tuple[Decimal, datetime]],
    now: datetime,
    max_quote_age_seconds: int,
) -> EquitySnapshot:
    """Value one currency without FX conversion; any missing/stale held mark blocks."""
    if currency not in {"KRW", "USD"} or now.tzinfo is None or max_quote_age_seconds <= 0:
        return EquitySnapshot(currency, None, "RISK_INPUT_INVALID")
    cash = [row for row in cash_balances if str(row.get("currency", "")).upper() == currency]
    if len(cash) != 1:
        return EquitySnapshot(currency, None, "PAPER_CASH_MISSING_OR_AMBIGUOUS")
    try:
        equity = Decimal(str(cash[0]["balance"]))
        if not equity.is_finite() or equity < 0:
            raise InvalidOperation
        for position in positions:
            if str(position.get("currency", "")).upper() != currency:
                continue
            quantity = int(position["quantity"])
            if quantity <= 0:
                return EquitySnapshot(currency, None, "PAPER_POSITION_INVALID")
            market = str(position["market"]).upper()
            symbol = str(position["symbol"]).upper()
            quote = quotes.get((market, symbol))
            if quote is None:
                return EquitySnapshot(currency, None, "POSITION_QUOTE_MISSING")
            price, quoted_at = quote
            if quoted_at.tzinfo is None or price <= 0 or not price.is_finite():
                return EquitySnapshot(currency, None, "POSITION_QUOTE_INVALID")
            age = (now.astimezone(timezone.utc) - quoted_at.astimezone(timezone.utc)).total_seconds()
            if age < -1 or age > max_quote_age_seconds:
                return EquitySnapshot(currency, None, "POSITION_QUOTE_STALE")
            equity += price * quantity
    except (KeyError, TypeError, ValueError, ArithmeticError, InvalidOperation):
        return EquitySnapshot(currency, None, "PAPER_LEDGER_INVALID")
    if equity <= 0:
        return EquitySnapshot(currency, None, "PAPER_EQUITY_NONPOSITIVE")
    return EquitySnapshot(currency, equity, None)


def daily_loss_percent(baseline: Decimal | None, current_equity: Decimal | None) -> Decimal | None:
    if (not isinstance(baseline, Decimal) or not isinstance(current_equity, Decimal)
            or not baseline.is_finite() or not current_equity.is_finite()
            or baseline <= 0 or current_equity < 0):
        return None
    return (baseline - current_equity) / baseline * Decimal(100)


def baseline_capture_open(
    *, market_open: datetime | None, now: datetime, window: timedelta = timedelta(minutes=3),
) -> bool:
    """Permit automatic session baseline only shortly after verified exchange open."""
    if market_open is None or market_open.tzinfo is None or now.tzinfo is None or window <= timedelta(0):
        return False
    current = now.astimezone(timezone.utc)
    opened = market_open.astimezone(timezone.utc)
    return opened <= current <= opened + window
