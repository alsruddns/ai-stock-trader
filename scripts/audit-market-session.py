from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

import psycopg
from app.market_calendar import exchange_continuous_session_window
from app.quant import MinuteBar
from app.session_quality import audit_minute_bar_session, meets_minimum_spread_coverage


def _load_local_env() -> None:
    """Load ignored local environment values without printing credentials."""
    env_file = repository / ".env_local"
    if not env_file.is_file():
        env_file = repository / ".env"
    if not env_file.is_file():
        raise ValueError("local .env_local or .env file is missing")
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
            continue
        name, value = trimmed.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit of persisted continuous-session bars and spread coverage."
    )
    parser.add_argument("--market", choices=("KR", "US"), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--session-date", type=date.fromisoformat, required=True)
    parser.add_argument("--require-complete", action="store_true",
                        help="return exit code 2 unless a closed full session is complete")
    parser.add_argument("--minimum-spread-coverage-percent", type=float,
                        help="return exit code 2 unless observed spread coverage meets this 0..100 percent threshold")
    args = parser.parse_args()

    symbol = args.symbol.strip().upper()
    if not symbol:
        parser.error("--symbol must not be empty")
    if args.market == "KR" and (len(symbol) != 6 or not symbol.isdigit()):
        parser.error("KR symbol must be a six-digit instrument code")
    if args.market == "US" and not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,15}", symbol):
        parser.error("US symbol is invalid")

    zone = ZoneInfo("Asia/Seoul" if args.market == "KR" else "America/New_York")
    session_noon = datetime.combine(args.session_date, time(hour=12), tzinfo=zone)
    window = exchange_continuous_session_window(args.market, session_noon)
    if window is None:
        parser.error("the requested date is not a calendar-confirmed exchange session")
    opened, closed = window

    try:
        _load_local_env()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not os.getenv("POSTGRES_PASSWORD"):
        parser.error("POSTGRES_PASSWORD is not configured in the ignored local environment file")

    try:
        with psycopg.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", "5432"),
            dbname=os.getenv("POSTGRES_DB", "ai_stock_trader"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.environ["POSTGRES_PASSWORD"],
            connect_timeout=5,
        ) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            rows = connection.execute(
                """SELECT bar_at, open_price, high_price, low_price, close_price,
                          volume, spread_bps, source
                     FROM market_minute_bar
                    WHERE market = %s AND symbol = %s AND bar_at >= %s AND bar_at < %s
                    ORDER BY bar_at""",
                (args.market, symbol, opened, closed),
            ).fetchall()
    except psycopg.Error:
        parser.error("PostgreSQL query failed; check the local database and migration state")

    bars = [MinuteBar(
        timestamp=row[0], open=row[1], high=row[2], low=row[3], close=row[4],
        volume=int(row[5]), spread_bps=row[6],
    ) for row in rows]
    source_counts: dict[str, int] = {}
    for row in rows:
        source = str(row[7]).strip() if row[7] is not None else ""
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1
    report = audit_minute_bar_session(
        args.market, symbol, args.session_date, bars, evaluated_at=datetime.now(timezone.utc),
    )
    report["source_counts"] = dict(sorted(source_counts.items()))
    spread_coverage_passed = None
    if args.minimum_spread_coverage_percent is not None:
        try:
            spread_coverage_passed = meets_minimum_spread_coverage(
                report["spread_coverage_percent"], args.minimum_spread_coverage_percent,
            )
        except ValueError as exc:
            parser.error(str(exc))
        report["minimum_spread_coverage_percent"] = args.minimum_spread_coverage_percent
        report["minimum_spread_coverage_met"] = spread_coverage_passed
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if ((args.require_complete and not report["full_session_complete"])
            or spread_coverage_passed is False):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
