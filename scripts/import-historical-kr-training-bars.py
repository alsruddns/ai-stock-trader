from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

import psycopg
from app.data_provenance import load_data_provenance, validate_model_session_integrity, validate_model_training_coverage
from app.market_calendar import exchange_continuous_session_window
from app.quant import MinuteBar

SOURCE = "KIS_PAPER_REST_MINUTE_CHART"
FILENAME = re.compile(r"kis-rest-kr-(\d{6})-(\d{8})-(\d{8})\.csv\Z")
CSV_FIELDS = [
    "symbol", "timestamp", "open", "high", "low", "close", "volume",
    "spread_bps", "spread_observed_at",
]


def _load_local_env() -> None:
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


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {field} in historical dataset") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def load_dataset(path: Path, expected_symbol: str) -> tuple[str, dict[date, list[MinuteBar]]]:
    match = FILENAME.fullmatch(path.name)
    if not match or match.group(1) != expected_symbol:
        raise ValueError("historical dataset filename does not match the requested KR symbol")
    from_date = date(int(match.group(2)[:4]), int(match.group(2)[4:6]), int(match.group(2)[6:8]))
    to_date = date(int(match.group(3)[:4]), int(match.group(3)[4:6]), int(match.group(3)[6:8]))
    if from_date > to_date:
        raise ValueError("historical dataset filename has an invalid date range")

    csv_bytes = path.read_bytes()
    digest = hashlib.sha256(csv_bytes).hexdigest()
    provenance = load_data_provenance(path, purpose="model_training", source_bytes=csv_bytes)
    sessions: dict[date, list[MinuteBar]] = defaultdict(list)
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("historical dataset must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != CSV_FIELDS:
        raise ValueError("historical dataset columns do not match the approved KIS export format")
    zone = ZoneInfo("Asia/Seoul")
    try:
        for row in reader:
            if row is None or set(row) != set(CSV_FIELDS) or row["symbol"] != expected_symbol:
                raise ValueError("historical dataset contains an unexpected symbol or row shape")
            timestamp = _parse_timestamp(row["timestamp"], "bar timestamp")
            session = timestamp.astimezone(zone).date()
            if not from_date <= session <= to_date:
                raise ValueError("historical dataset contains a bar outside its filename date range")
            spread = Decimal(row["spread_bps"]) if row["spread_bps"].strip() else None
            observed_at = _parse_timestamp(row["spread_observed_at"], "spread observation time") \
                if row["spread_observed_at"].strip() else None
            if (spread is None) != (observed_at is None):
                raise ValueError("historical spread and observation time must both be present or both absent")
            try:
                bar = MinuteBar(
                    timestamp=timestamp,
                    open=Decimal(row["open"]), high=Decimal(row["high"]),
                    low=Decimal(row["low"]), close=Decimal(row["close"]),
                    volume=int(row["volume"], 10), spread_bps=spread,
                    spread_observed_at=observed_at,
                )
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("historical dataset contains an invalid OHLCV bar") from exc
            sessions[session].append(bar)
    except (KeyError, AttributeError, InvalidOperation, TypeError) as exc:
        raise ValueError("historical dataset contains malformed CSV fields") from exc

    if not sessions:
        raise ValueError("historical dataset contains no bars")
    if any(session < from_date or session > to_date for session in sessions):
        raise ValueError("historical dataset session falls outside its filename date range")
    validate_model_session_integrity(market="KR", symbol=expected_symbol, sessions=sessions)
    validate_model_training_coverage(
        provenance, market="KR", sessions=sessions, minimum_sessions=1,
    )
    if any(
        source_counts != {SOURCE: len(sessions[session])}
        for session, source_counts in (
            (date.fromisoformat(coverage["session_date"]), coverage.get("source_counts"))
            for coverage in provenance["coverage"]["sessions"]
        )
    ):
        raise ValueError("historical dataset provenance does not identify the approved KIS REST source")
    return digest, sessions


def _session_window(session: date) -> tuple[datetime, datetime]:
    noon = datetime.combine(session, time(hour=12), tzinfo=ZoneInfo("Asia/Seoul"))
    window = exchange_continuous_session_window("KR", noon)
    if window is None:
        raise ValueError("historical dataset contains a non-session date")
    return window


def _connect() -> psycopg.Connection:
    if not os.getenv("POSTGRES_PASSWORD"):
        raise ValueError("POSTGRES_PASSWORD is not configured in the ignored local environment file")
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "ai_stock_trader"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=5,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import only absent, fully validated historical KR KIS REST sessions for local training."
    )
    parser.add_argument("--symbol", required=True, choices=("000660", "005930"))
    parser.add_argument("--apply", action="store_true", help="write missing complete sessions; default is read-only dry-run")
    args = parser.parse_args()

    data_dir = repository / "data" / "backtests" / "kis-rest-kr"
    paths = sorted(data_dir.glob(f"kis-rest-kr-{args.symbol}-*.csv"))
    if not paths:
        parser.error("no local historical CSV files were found for the requested symbol")
    try:
        _load_local_env()
        datasets = [(path, *load_dataset(path, args.symbol)) for path in paths]
        seen_sessions: set[date] = set()
        for _, _, sessions in datasets:
            overlap = seen_sessions.intersection(sessions)
            if overlap:
                raise ValueError("historical CSV files overlap on one or more exchange sessions")
            seen_sessions.update(sessions)
        if args.apply:
            from app.market_scope import require_cli_operational_market
            require_cli_operational_market("KR", "historical KR training-data import")
        with _connect() as connection:
            if not args.apply:
                connection.execute("SET TRANSACTION READ ONLY")
            plan: list[dict[str, object]] = []
            for path, digest, sessions in datasets:
                for session, bars in sorted(sessions.items()):
                    opened, closed = _session_window(session)
                    existing = connection.execute(
                        """SELECT count(*) FROM market_minute_bar
                            WHERE market = 'KR' AND symbol = %s AND bar_at >= %s AND bar_at < %s""",
                        (args.symbol, opened, closed),
                    ).fetchone()[0]
                    plan.append({
                        "file": path.name, "sha256": digest, "session_date": session.isoformat(),
                        "expected_bars": len(bars), "existing_bars": int(existing),
                        "action": "skip_existing_session" if existing else "insert_complete_session",
                    })

            inserted_total = 0
            if args.apply:
                for item in plan:
                    if item["action"] != "insert_complete_session":
                        continue
                    session = date.fromisoformat(str(item["session_date"]))
                    current = next(
                        session_bars
                        for path, _, sessions in datasets if path.name == item["file"]
                        for session_date, session_bars in sessions.items() if session_date == session
                    )
                    cursor = connection.cursor()
                    cursor.executemany(
                        """INSERT INTO market_minute_bar
                               (market, symbol, bar_at, open_price, high_price, low_price, close_price,
                                volume, spread_bps, source, spread_observed_at)
                           VALUES ('KR', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (market, symbol, bar_at) DO NOTHING""",
                        [
                            (args.symbol, bar.timestamp, bar.open, bar.high, bar.low, bar.close,
                             bar.volume, bar.spread_bps, SOURCE, bar.spread_observed_at)
                            for bar in current
                        ],
                    )
                    inserted_count = cursor.rowcount
                    cursor.close()
                    if inserted_count != len(current):
                        raise RuntimeError(
                            f"session {session.isoformat()} changed during import; transaction rolled back"
                        )
                    inserted_total += inserted_count
                    item["inserted_bars"] = inserted_count

            print(json.dumps({
                "market": "KR", "symbol": args.symbol,
                "mode": "APPLIED" if args.apply else "DRY_RUN",
                "dataset_count": len(datasets), "session_count": len(plan),
                "complete_missing_sessions": sum(item["action"] == "insert_complete_session" for item in plan),
                "skipped_existing_sessions": sum(item["action"] == "skip_existing_session" for item in plan),
                "inserted_bars": inserted_total, "sessions": plan,
            }, ensure_ascii=False, indent=2))
    except (OSError, ValueError, psycopg.Error, RuntimeError) as exc:
        parser.error(str(exc) if isinstance(exc, ValueError) else "historical data import failed; transaction was not committed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
