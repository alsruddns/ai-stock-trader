"""Optional PostgreSQL persistence for read-only KIS snapshots."""

from __future__ import annotations

import os
from datetime import date, datetime, time as datetime_time, timedelta, timezone
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
                volume, spread_bps, spread_observed_at, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (market, symbol, bar_at) DO UPDATE SET
                 open_price = EXCLUDED.open_price, high_price = EXCLUDED.high_price,
                 low_price = EXCLUDED.low_price, close_price = EXCLUDED.close_price,
                 volume = EXCLUDED.volume,
                 spread_bps = CASE
                     WHEN EXCLUDED.spread_observed_at IS NOT NULL OR market_minute_bar.spread_bps IS NULL
                     THEN EXCLUDED.spread_bps ELSE market_minute_bar.spread_bps END,
                 spread_observed_at = COALESCE(EXCLUDED.spread_observed_at, market_minute_bar.spread_observed_at),
                 source = EXCLUDED.source, stored_at = now()
               WHERE market_minute_bar.spread_bps IS NULL OR EXCLUDED.spread_observed_at IS NOT NULL""",
                [(market, symbol.upper(), bar.timestamp, bar.open, bar.high, bar.low, bar.close,
                  bar.volume, bar.spread_bps, bar.spread_observed_at, source) for bar in regular_session_bars],
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
        connection.execute("SET TRANSACTION READ ONLY", None)
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT bar_at, open_price, high_price, low_price, close_price, volume, spread_bps,
                          spread_observed_at
                   FROM market_minute_bar
                   WHERE market = %s AND symbol = %s AND bar_at >= %s AND bar_at < %s
                   ORDER BY bar_at DESC LIMIT %s""",
                (market, symbol.upper(), session_start, session_end, limit),
            )
            rows = cursor.fetchall()
    return [MinuteBar(
        timestamp=row[0], open=row[1], high=row[2], low=row[3], close=row[4],
        volume=int(row[5]), spread_bps=row[6], spread_observed_at=row[7],
    ) for row in reversed(rows)]


def load_minute_bar_training_history(
    market: str, symbol: str, from_date: date, to_date: date, *, limit: int = 60000,
) -> dict[date, dict[str, object]]:
    """Load bounded stored bars and source counts in a read-only transaction."""
    if (market not in {"KR", "US"} or not symbol.strip() or from_date > to_date
            or (to_date - from_date).days > 120 or not 1 <= limit <= 60000):
        raise ValueError("market, symbol, date range, and bounded row limit are required")
    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    range_start = datetime.combine(from_date, datetime_time.min, tzinfo=zone)
    range_end = datetime.combine(to_date + timedelta(days=1), datetime_time.min, tzinfo=zone)
    with _connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY", None)
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT bar_at, open_price, high_price, low_price, close_price,
                          volume, spread_bps, spread_observed_at, source
                     FROM market_minute_bar
                    WHERE market = %s AND symbol = %s AND bar_at >= %s AND bar_at < %s
                    ORDER BY bar_at
                    LIMIT %s""",
                (market, symbol.upper(), range_start, range_end, limit + 1),
            )
            rows = cursor.fetchall()
    if len(rows) > limit:
        raise RuntimeError("stored market-data history exceeds the bounded audit limit")

    sessions: dict[date, dict[str, object]] = {}
    session_windows: dict[date, tuple[datetime, datetime] | None] = {}
    for timestamp, open_price, high_price, low_price, close_price, volume, spread_bps, spread_observed_at, source in rows:
        session_date = timestamp.astimezone(zone).date()
        if session_date not in session_windows:
            session_noon = datetime.combine(session_date, datetime_time(hour=12), tzinfo=zone)
            session_windows[session_date] = exchange_continuous_session_window(market, session_noon)
        session_window = session_windows[session_date]
        if session_window is None or not session_window[0] <= timestamp < session_window[1]:
            continue
        entry = sessions.setdefault(session_date, {"bars": [], "source_counts": {}})
        bars = entry["bars"]
        source_counts = entry["source_counts"]
        assert isinstance(bars, list) and isinstance(source_counts, dict)
        bars.append(MinuteBar(
            timestamp=timestamp, open=open_price, high=high_price, low=low_price, close=close_price,
            volume=int(volume), spread_bps=spread_bps, spread_observed_at=spread_observed_at,
        ))
        if source:
            source_counts[str(source)] = int(source_counts.get(str(source), 0)) + 1
    return sessions


def load_session_minute_bars_for_symbols(
    market: str, symbols: list[str], session_date: date,
) -> dict[str, list[MinuteBar]]:
    """Load up to 50 symbols in one read-only query for dashboard quality summaries."""
    if market not in {"KR", "US"} or not 1 <= len(symbols) <= 50:
        raise ValueError("market and 1 to 50 symbols are required")
    normalized_symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    if any(not symbol for symbol in normalized_symbols):
        raise ValueError("symbols must not be empty")
    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    session_noon = datetime.combine(session_date, datetime_time(hour=12), tzinfo=zone)
    window = exchange_continuous_session_window(market, session_noon)
    result = {symbol: [] for symbol in normalized_symbols}
    if window is None:
        return result
    session_start, session_end = window
    with _connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY", None)
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT symbol, bar_at, open_price, high_price, low_price, close_price, volume, spread_bps,
                          spread_observed_at
                   FROM market_minute_bar
                   WHERE market = %s AND symbol = ANY(%s) AND bar_at >= %s AND bar_at < %s
                   ORDER BY symbol, bar_at""",
                (market, normalized_symbols, session_start, session_end),
            )
            rows = cursor.fetchall()
    for row in rows:
        symbol = str(row[0]).upper()
        if symbol not in result:
            continue
        result[symbol].append(MinuteBar(
            timestamp=row[1], open=row[2], high=row[3], low=row[4], close=row[5],
            volume=int(row[6]), spread_bps=row[7], spread_observed_at=row[8],
        ))
    return result


def load_session_minute_bar_source_counts(
    market: str, symbols: list[str], session_date: date,
) -> dict[str, dict[str, int]]:
    """Count persisted bar provenance per watched symbol for one local session."""
    if market not in {"KR", "US"} or not 1 <= len(symbols) <= 50:
        raise ValueError("market and 1 to 50 symbols are required")
    normalized_symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    if any(not symbol for symbol in normalized_symbols):
        raise ValueError("symbols must not be empty")
    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    session_noon = datetime.combine(session_date, datetime_time(hour=12), tzinfo=zone)
    window = exchange_continuous_session_window(market, session_noon)
    result = {symbol: {} for symbol in normalized_symbols}
    if window is None:
        return result
    session_start, session_end = window
    with _connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY", None)
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT symbol, source, count(*)
                   FROM market_minute_bar
                   WHERE market = %s AND symbol = ANY(%s) AND bar_at >= %s AND bar_at < %s
                   GROUP BY symbol, source
                   ORDER BY symbol, source""",
                (market, normalized_symbols, session_start, session_end),
            )
            rows = cursor.fetchall()
    for symbol, source, count in rows:
        normalized_symbol = str(symbol).upper()
        if normalized_symbol in result:
            result[normalized_symbol][str(source)] = int(count)
    return result


def store_account_balance(balance: AccountBalance) -> None:
    with _connect() as connection:
        connection.execute(
            """INSERT INTO account_balance_snapshot
               (environment, market, currency, summary, positions, retrieved_at, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (balance.environment, balance.market, balance.currency, Jsonb(balance.summary),
             Jsonb(balance.positions), balance.retrieved_at, balance.source),
        )
