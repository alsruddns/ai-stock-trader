"""Small local journal for same-session discovered candidate subscriptions."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import date
from pathlib import Path


class CandidateWatchStore:
    """Persist domestic candidate symbols locally, scoped to one exchange date."""

    def __init__(self, path: str | Path | None = None) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        configured_path = os.getenv("AI_STOCK_TRADER_CANDIDATE_WATCH_PATH", "").strip()
        self.path = Path(path or configured_path or repo_root / ".local" / "state" / "candidate-watches.sqlite3")
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS discovered_candidate_watch (
                   market TEXT NOT NULL CHECK (market = 'KR'),
                   session_date TEXT NOT NULL,
                   symbol TEXT NOT NULL,
                   PRIMARY KEY (market, session_date, symbol)
               )"""
        )
        return connection

    @staticmethod
    def _session_key(session_date: date) -> str:
        if not isinstance(session_date, date):
            raise ValueError("session date is required")
        return session_date.isoformat()

    @staticmethod
    def _symbols(symbols: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        normalized = tuple(sorted({symbol.strip().upper() for symbol in symbols}))
        if any(re.fullmatch(r"\d{6}", symbol) is None for symbol in normalized):
            raise ValueError("candidate symbols must be six-digit domestic codes")
        if len(normalized) > 20:
            raise ValueError("at most 20 candidate symbols can be stored")
        return normalized

    def replace(self, session_date: date, symbols: tuple[str, ...] | list[str]) -> None:
        session_key = self._session_key(session_date)
        normalized = self._symbols(symbols)
        with self._lock:
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        "DELETE FROM discovered_candidate_watch WHERE market = 'KR' AND session_date = ?",
                        (session_key,),
                    )
                    connection.executemany(
                        "INSERT INTO discovered_candidate_watch (market, session_date, symbol) VALUES ('KR', ?, ?)",
                        ((session_key, symbol) for symbol in normalized),
                    )
            finally:
                connection.close()

    def load(self, session_date: date) -> tuple[str, ...]:
        session_key = self._session_key(session_date)
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """SELECT symbol FROM discovered_candidate_watch
                       WHERE market = 'KR' AND session_date = ? ORDER BY symbol""",
                    (session_key,),
                ).fetchall()
                return tuple(str(row[0]) for row in rows)
            finally:
                connection.close()
