"""Optional PostgreSQL persistence for read-only KIS snapshots."""

from __future__ import annotations

import os

import psycopg
from psycopg.types.json import Jsonb

from .kis import AccountBalance, Quote


def enabled() -> bool:
    return os.getenv("KIS_SNAPSHOT_PERSISTENCE_ENABLED", "false").strip().lower() == "true"


def _connect() -> psycopg.Connection:
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "ai_stock_trader"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        connect_timeout=3,
    )


def store_quote(quote: Quote) -> None:
    with _connect() as connection:
        connection.execute(
            """INSERT INTO market_quote_snapshot
               (market, symbol, exchange, currency, price, change_amount, change_percent, volume, retrieved_at, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (quote.market, quote.symbol, quote.exchange, quote.currency, quote.price, quote.change,
             quote.change_percent, quote.volume, quote.retrieved_at, quote.source),
        )


def store_account_balance(balance: AccountBalance) -> None:
    with _connect() as connection:
        connection.execute(
            """INSERT INTO account_balance_snapshot
               (environment, market, currency, summary, positions, retrieved_at, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (balance.environment, balance.market, balance.currency, Jsonb(balance.summary),
             Jsonb(balance.positions), balance.retrieved_at, balance.source),
        )
