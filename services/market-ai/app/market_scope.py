"""Runtime market scope for APIs that fetch or validate operational market data."""

from __future__ import annotations

import os

from fastapi import HTTPException


def allowed_operational_markets() -> frozenset[str]:
    raw = os.getenv("MARKET_OPERATIONAL_SCOPE", "KR")
    values = frozenset(item.strip().upper() for item in raw.split(",") if item.strip())
    if not values or not values <= {"KR", "US"}:
        raise HTTPException(status_code=503, detail="Market operational scope configuration is invalid")
    return values


def require_operational_market(market: str, operation: str) -> None:
    normalized = market.strip().upper()
    if normalized not in allowed_operational_markets():
        raise HTTPException(
            status_code=423,
            detail=f"{operation} is disabled for {normalized}; current operational markets are domestic KR only",
        )


def require_cli_operational_market(market: str, operation: str) -> None:
    """Apply the same scope gate to offline command-line validation tools."""
    try:
        require_operational_market(market, operation)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc
