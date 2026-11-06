from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

import psycopg
from app.data_export import write_minute_bar_export
from app.market_calendar import exchange_continuous_session_window


def _load_local_env() -> None:
    """Load ignored local environment values for this process without printing them."""
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
        description="Export stored local minute bars as a CSV with an unreviewed rights sidecar."
    )
    parser.add_argument("--market", choices=("KR", "US"), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--to-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True,
                        help="CSV destination under the ignored local data/ directory")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing CSV and sidecar")
    parser.add_argument("--dry-run", action="store_true", help="query PostgreSQL and report row counts without writing files")
    args = parser.parse_args()

    market = args.market
    symbol = args.symbol.strip().upper()
    if market == "KR" and not re.fullmatch(r"\d{6}", symbol):
        parser.error("KR symbol must be a six-digit instrument code")
    if market == "US" and not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,15}", symbol):
        parser.error("US symbol must contain 1 to 16 letters, digits, dots, or hyphens")
    if args.from_date > args.to_date:
        parser.error("--from-date must be on or before --to-date")
    try:
        end_date = args.to_date + timedelta(days=1)
    except OverflowError:
        parser.error("--to-date is outside the supported range")

    output_path = args.output if args.output.is_absolute() else repository / args.output
    output_path = output_path.resolve()
    data_root = (repository / "data").resolve()
    if output_path.suffix.lower() != ".csv" or not output_path.is_relative_to(data_root):
        parser.error("--output must be a .csv file under the ignored repository data/ directory")
    sidecar_path = output_path.with_name(output_path.name + ".provenance.json")
    if not args.dry_run and not args.overwrite and (output_path.exists() or sidecar_path.exists()):
        parser.error("output already exists; pass --overwrite to replace it")

    try:
        _load_local_env()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not os.getenv("POSTGRES_PASSWORD"):
        parser.error("POSTGRES_PASSWORD is not configured in the ignored local environment file")

    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    start_at = datetime.combine(args.from_date, time.min, tzinfo=zone)
    end_at = datetime.combine(end_date, time.min, tzinfo=zone)
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
                (market, symbol, start_at, end_at),
            ).fetchall()
    except psycopg.Error:
        parser.error("PostgreSQL query failed; check the local database and migration state")

    rows = [
        row for row in rows
        if (window := exchange_continuous_session_window(market, row[0])) is not None
        and window[0] <= row[0].astimezone(ZoneInfo("UTC")) < window[1]
    ]

    sources = sorted({row[7] for row in rows if row[7]})
    if args.dry_run:
        print(json.dumps({
            "market": market,
            "symbol": symbol,
            "row_count": len(rows),
            "sources": sources,
            "dry_run": True,
            "files_written": False,
        }, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        parser.error("no stored minute bars matched this market, symbol, and local session-date range")
    try:
        sidecar = write_minute_bar_export(
            output_path, rows, market=market, symbol=symbol,
            from_date=args.from_date, to_date=args.to_date, sources=sources,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(json.dumps({
        "market": market,
        "symbol": symbol,
        "row_count": len(rows),
        "sources": sources,
        "csv_path": str(output_path.relative_to(repository)),
        "provenance_path": str(sidecar_path.relative_to(repository)),
        "dataset_sha256": sidecar["dataset_sha256"],
        "rights_reviewed": sidecar["rights_reviewed"],
        "permissions": sidecar["permissions"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
