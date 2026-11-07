"""One-shot REAL US quote diagnostic. This module has no order or account APIs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "market-ai"))

from app.kis import (KisApiError, KisReadOnlyClient, KisSettings,
                     KisSharedRateLimitUnavailable)  # noqa: E402


def validate_configuration(environment: Mapping[str, str]) -> None:
    required_false = (
        "LIVE_TRADING_ENABLED",
        "KIS_PAPER_ORDERS_ENABLED",
        "KIS_REAL_ORDERS_ENABLED",
        "KIS_REAL_RECONCILIATION_ENABLED",
        "KIS_REAL_ACCOUNT_READ_ENABLED",
        "PAPER_AUTOTRADING_ENABLED",
    )
    if environment.get("TRADING_MODE", "paper").strip().lower() != "paper":
        raise ValueError("REAL quote probe requires PAPER trading mode")
    if any(environment.get(name, "false").strip().lower() != "false" for name in required_false):
        raise ValueError("REAL quote probe requires every order, account-read, and automation gate disabled")
    if environment.get("KIS_MARKET_DATA_ENABLED", "false").strip().lower() != "true":
        raise ValueError("KIS market data must be enabled for this process")
    if environment.get("KIS_REAL_MARKET_DATA_ENABLED", "false").strip().lower() != "true":
        raise ValueError("REAL market data requires a process-only opt-in")
    if environment.get("KIS_SHARED_RATE_LIMIT_ENABLED", "true").strip().lower() != "true":
        raise ValueError("The shared PostgreSQL KIS rate limiter must remain enabled")
    if not environment.get("KIS_REAL_APP_KEY") or not environment.get("KIS_REAL_APP_SECRET"):
        raise ValueError("REAL KIS market-data credentials are not configured")
    if not environment.get("POSTGRES_PASSWORD"):
        raise ValueError("PostgreSQL credentials are required for the shared KIS rate limiter")


def safe_success_result(symbol: str, exchange: str, currency: str, retrieved_at: str,
                        has_spread: bool) -> dict[str, object]:
    return {
        "status": "ok",
        "environment": "real_market_data_read_only",
        "market": "US",
        "symbol": symbol,
        "exchange": exchange,
        "currency": currency,
        "price_returned": True,
        "spread_returned": has_spread,
        "source": "KIS_REST_SNAPSHOT",
        "retrieved_at": retrieved_at,
        "orders_called": False,
        "account_reads_called": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--exchange", choices=("NAS", "NYS", "AMS"), default="NAS")
    arguments = parser.parse_args()
    symbol = arguments.symbol.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,15}", symbol):
        parser.error("symbol must contain 1 to 16 letters, digits, dots, or hyphens")

    try:
        validate_configuration(os.environ)
        settings = KisSettings.from_environment("real")
        client = KisReadOnlyClient(settings)
        try:
            quote = client.get_quote("US", symbol, arguments.exchange)
        finally:
            client.close()
        result = safe_success_result(
            quote.symbol, quote.exchange or arguments.exchange, quote.currency,
            quote.retrieved_at.astimezone(timezone.utc).isoformat(),
            quote.best_ask is not None and quote.best_bid is not None,
        )
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (KisApiError, KisSharedRateLimitUnavailable, ValueError) as exc:
        broker_code = getattr(exc, "broker_code", None)
        if not isinstance(broker_code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", broker_code):
            broker_code = None
        failure = {
            "status": "request_failed",
            "environment": "real_market_data_read_only",
            "failure_type": type(exc).__name__,
            "broker_code": broker_code,
            "orders_called": False,
            "account_reads_called": False,
        }
        print(json.dumps(failure, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
