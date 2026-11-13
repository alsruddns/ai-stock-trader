"""Small durable local journal for minute bars awaiting PostgreSQL acknowledgement."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .quant import MinuteBar


class BarWriteOutboxFull(RuntimeError):
    """Raised when the bounded local journal has reached its configured capacity."""


class BarWriteOutbox:
    """SQLite WAL outbox; PostgreSQL remains the query and training-data store."""

    def __init__(self, path: str | Path | None = None, *, maximum_pending: int = 10_000) -> None:
        if maximum_pending < 1:
            raise ValueError("maximum_pending must be positive")
        repo_root = Path(__file__).resolve().parents[3]
        configured_path = os.getenv("AI_STOCK_TRADER_BAR_OUTBOX_PATH", "").strip()
        self.path = Path(path or configured_path or repo_root / ".local" / "state" / "bar-write-outbox.sqlite3")
        self.maximum_pending = maximum_pending
        self._lock = threading.RLock()

    @staticmethod
    def _bar_key(timestamp: datetime) -> str:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("bar timestamp must include a timezone")
        return timestamp.astimezone(timezone.utc).isoformat()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS pending_minute_bar (
                   market TEXT NOT NULL,
                   symbol TEXT NOT NULL,
                   bar_at_utc TEXT NOT NULL,
                   open_price TEXT NOT NULL,
                   high_price TEXT NOT NULL,
                   low_price TEXT NOT NULL,
                   close_price TEXT NOT NULL,
                   volume INTEGER NOT NULL,
                   spread_bps TEXT,
                   spread_observed_at TEXT,
                   source TEXT NOT NULL,
                   created_at_utc TEXT NOT NULL,
                   PRIMARY KEY (market, symbol, bar_at_utc)
               )"""
        )
        return connection

    def enqueue(self, market: str, symbol: str, bar: MinuteBar, *, source: str) -> int:
        normalized_market = market.strip().upper()
        normalized_symbol = symbol.strip().upper()
        if normalized_market not in {"KR", "US"} or not normalized_symbol or not source.strip():
            raise ValueError("market, symbol, and source are required")
        key = self._bar_key(bar.timestamp)
        with self._lock:
            connection = self._connect()
            try:
                with connection:
                    existing = connection.execute(
                        "SELECT 1 FROM pending_minute_bar WHERE market = ? AND symbol = ? AND bar_at_utc = ?",
                        (normalized_market, normalized_symbol, key),
                    ).fetchone()
                    count = int(connection.execute("SELECT count(*) FROM pending_minute_bar").fetchone()[0])
                    if existing is None and count >= self.maximum_pending:
                        raise BarWriteOutboxFull("local minute-bar outbox is full")
                    connection.execute(
                        """INSERT OR IGNORE INTO pending_minute_bar
                           (market, symbol, bar_at_utc, open_price, high_price, low_price, close_price,
                            volume, spread_bps, spread_observed_at, source, created_at_utc)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (normalized_market, normalized_symbol, key, str(bar.open), str(bar.high),
                         str(bar.low), str(bar.close), int(bar.volume),
                         str(bar.spread_bps) if bar.spread_bps is not None else None,
                         bar.spread_observed_at.isoformat() if bar.spread_observed_at else None,
                         source.strip(), datetime.now(timezone.utc).isoformat()),
                    )
                    return int(connection.execute("SELECT count(*) FROM pending_minute_bar").fetchone()[0])
            finally:
                connection.close()

    def pending(self, *, limit: int | None = None) -> list[tuple[str, str, MinuteBar, str]]:
        resolved_limit = self.maximum_pending if limit is None else limit
        if not 1 <= resolved_limit <= self.maximum_pending:
            raise ValueError("pending limit is outside the configured bound")
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA query_only=ON")
                rows = connection.execute(
                    """SELECT market, symbol, bar_at_utc, open_price, high_price, low_price, close_price,
                              volume, spread_bps, spread_observed_at, source
                       FROM pending_minute_bar ORDER BY bar_at_utc, market, symbol LIMIT ?""",
                    (resolved_limit,),
                ).fetchall()
            finally:
                connection.close()
        return [
            (market, symbol, MinuteBar(
                datetime.fromisoformat(bar_at_utc), Decimal(open_price), Decimal(high_price),
                Decimal(low_price), Decimal(close_price), int(volume),
                Decimal(spread_bps) if spread_bps is not None else None,
                datetime.fromisoformat(spread_observed_at) if spread_observed_at else None,
            ), source)
            for (market, symbol, bar_at_utc, open_price, high_price, low_price, close_price,
                 volume, spread_bps, spread_observed_at, source) in rows
        ]

    def acknowledge(self, market: str, symbol: str, timestamp: datetime) -> int:
        with self._lock:
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        "DELETE FROM pending_minute_bar WHERE market = ? AND symbol = ? AND bar_at_utc = ?",
                        (market.strip().upper(), symbol.strip().upper(), self._bar_key(timestamp)),
                    )
                    return int(connection.execute("SELECT count(*) FROM pending_minute_bar").fetchone()[0])
            finally:
                connection.close()

    def count(self) -> int:
        with self._lock:
            connection = self._connect()
            try:
                return int(connection.execute("SELECT count(*) FROM pending_minute_bar").fetchone()[0])
            finally:
                connection.close()
