"""Optional PostgreSQL persistence for read-only KIS snapshots."""

from __future__ import annotations

import os
from datetime import date, datetime, time as datetime_time, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from .kis import AccountBalance, Quote
from .market_calendar import exchange_continuous_session_window
from .quant import MinuteBar


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


def store_minute_bars(market: str, symbol: str, bars: list[MinuteBar], *, source: str) -> None:
    if market not in {"KR", "US"} or not symbol.strip() or not source.strip():
        raise ValueError("market, symbol, and source are required")
    if not bars:
        return
    regular_session_bars = []
    for bar in bars:
        window = exchange_continuous_session_window(market, bar.timestamp)
        if window is None:
            continue
        timestamp = bar.timestamp.astimezone(timezone.utc)
        if window[0] <= timestamp < window[1]:
            regular_session_bars.append(bar)
    if not regular_session_bars:
        return
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO market_minute_bar
               (market, symbol, bar_at, open_price, high_price, low_price, close_price,
                volume, spread_bps, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (market, symbol, bar_at) DO UPDATE SET
                 open_price = EXCLUDED.open_price, high_price = EXCLUDED.high_price,
                 low_price = EXCLUDED.low_price, close_price = EXCLUDED.close_price,
                 volume = EXCLUDED.volume, spread_bps = EXCLUDED.spread_bps,
                 source = EXCLUDED.source, stored_at = now()
               WHERE market_minute_bar.spread_bps IS NULL OR EXCLUDED.spread_bps IS NOT NULL""",
                [(market, symbol.upper(), bar.timestamp, bar.open, bar.high, bar.low, bar.close,
                  bar.volume, bar.spread_bps, source) for bar in regular_session_bars],
            )


def load_session_minute_bars(market: str, symbol: str, session_date: date, *, limit: int = 5000) -> list[MinuteBar]:
    """Load persisted closed bars for one local exchange session, oldest first."""
    if market not in {"KR", "US"} or not symbol.strip() or not 1 <= limit <= 5000:
        raise ValueError("market, symbol, and a bounded bar limit are required")
    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    session_noon = datetime.combine(session_date, datetime_time(hour=12), tzinfo=zone)
    window = exchange_continuous_session_window(market, session_noon)
    if window is None:
        return []
    session_start, session_end = window
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT bar_at, open_price, high_price, low_price, close_price, volume, spread_bps
                   FROM market_minute_bar
                   WHERE market = %s AND symbol = %s AND bar_at >= %s AND bar_at < %s
                   ORDER BY bar_at DESC LIMIT %s""",
                (market, symbol.upper(), session_start, session_end, limit),
            )
            rows = cursor.fetchall()
    return [MinuteBar(
        timestamp=row[0], open=row[1], high=row[2], low=row[3], close=row[4],
        volume=int(row[5]), spread_bps=row[6],
    ) for row in reversed(rows)]


def store_account_balance(balance: AccountBalance) -> None:
    with _connect() as connection:
        connection.execute(
            """INSERT INTO account_balance_snapshot
               (environment, market, currency, summary, positions, retrieved_at, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (balance.environment, balance.market, balance.currency, Jsonb(balance.summary),
             Jsonb(balance.positions), balance.retrieved_at, balance.source),
        )
