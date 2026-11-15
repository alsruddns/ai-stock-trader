from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

from app.data_export import write_minute_bar_export
from app.kis import KisApiError, KisReadOnlyClient, KisSettings
from app.market_calendar import exchange_continuous_session_window, exchange_session_schedule

KIS_REST_MINUTE_SOURCE = "KIS_PAPER_REST_MINUTE_CHART"
SOURCE_REFERENCE = (
    "KIS PAPER read-only REST endpoint /uapi/domestic-stock/v1/quotations/"
    "inquire-time-dailychartprice; TR FHKST03010230; historical minute bars do not include spreads"
)
KST = ZoneInfo("Asia/Seoul")


def _load_local_environment() -> None:
    env_file = repository / ".env_local"
    if not env_file.is_file():
        env_file = repository / ".env"
    if not env_file.is_file():
        raise ValueError("Repository .env_local or .env file is missing")
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if name.strip() and name.strip() not in os.environ:
            os.environ[name.strip()] = value


def _complete_session_bars(session_date: date, bars: list) -> bool:
    window = exchange_continuous_session_window(
        "KR", datetime.combine(session_date, time(hour=12), tzinfo=KST),
    )
    if window is None:
        return False
    opened, closed = (point.astimezone(timezone.utc) for point in window)
    expected_count = int((closed - opened).total_seconds() // 60)
    expected = [opened + timedelta(minutes=index) for index in range(expected_count)]
    actual = [bar.timestamp.astimezone(timezone.utc) for bar in bars]
    return actual == expected and all(bar.spread_bps is None for bar in bars)


def _load_safety_settings(parser: argparse.ArgumentParser) -> KisReadOnlyClient:
    try:
        _load_local_environment()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    required = {
        "TRADING_MODE": "paper",
        "LIVE_TRADING_ENABLED": "false",
        "KIS_ENVIRONMENT": "paper",
        "KIS_MARKET_DATA_ENABLED": "true",
        "KIS_PAPER_ORDERS_ENABLED": "false",
        "KIS_REAL_ORDERS_ENABLED": "false",
        "PAPER_AUTOTRADING_ENABLED": "false",
    }
    for name, expected in required.items():
        if os.getenv(name, "false").strip().lower() != expected:
            parser.error(f"{name} must remain {expected} for this read-only export")
    try:
        settings = KisSettings.from_environment("paper")
        if not settings.enabled or not settings.app_key or not settings.app_secret:
            parser.error("PAPER market data and KIS credentials must be configured in .env_local")
        return KisReadOnlyClient(settings)
    except (KisApiError, OSError, ValueError) as exc:
        parser.error(f"PAPER read-only client could not be configured ({type(exc).__name__})")
    raise AssertionError("argparse.error must exit")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=("Export complete historical KR PAPER KIS minute bars to ignored local CSV files; "
                     "no account or order endpoints are used."),
    )
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--to-date", type=date.fromisoformat, required=True)
    parser.add_argument("--symbol", action="append", required=True,
                        help="repeat for each six-digit domestic stock symbol; at least two are required")
    parser.add_argument("--output-directory", type=Path, default=Path("data/backtests/kis-rest-kr"),
                        help="local ignored output directory under data/backtests/")
    parser.add_argument("--minimum-shared-sessions", type=int, default=5)
    args = parser.parse_args()

    symbols = [symbol.strip() for symbol in args.symbol]
    if len(symbols) < 2 or len(symbols) > 5 or len(set(symbols)) != len(symbols):
        parser.error("provide two to five distinct KR symbols")
    if any(not re.fullmatch(r"\d{6}", symbol) for symbol in symbols):
        parser.error("each KR symbol must contain exactly six digits")
    if args.from_date > args.to_date:
        parser.error("--from-date must be on or before --to-date")
    if (args.to_date - args.from_date).days > 45:
        parser.error("historical export range is limited to 46 calendar days")
    if not 1 <= args.minimum_shared_sessions <= 15:
        parser.error("--minimum-shared-sessions must be between 1 and 15")

    output_directory = args.output_directory
    if not output_directory.is_absolute():
        output_directory = repository / output_directory
    output_directory = output_directory.resolve()
    backtest_root = (repository / "data" / "backtests").resolve()
    if not output_directory.is_relative_to(backtest_root):
        parser.error("output directory must be under the ignored repository data/backtests/ directory")

    now_utc = datetime.now(timezone.utc)
    try:
        schedule = exchange_session_schedule("KR", args.from_date, args.to_date)
    except (RuntimeError, ValueError) as exc:
        parser.error(f"KR exchange calendar could not verify the requested range ({type(exc).__name__})")
    closed_dates = [session_date for session_date, _opened, closed in schedule if closed <= now_utc]
    if not closed_dates:
        parser.error("the requested range contains no closed KR trading sessions")
    if len(closed_dates) > 15:
        parser.error("at most 15 closed KR sessions may be fetched per invocation")
    if args.minimum_shared_sessions > len(closed_dates):
        parser.error("the requested range has fewer closed sessions than the minimum shared-session requirement")

    output_paths = {
        symbol: output_directory / (
            f"kis-rest-kr-{symbol}-{closed_dates[0]:%Y%m%d}-{closed_dates[-1]:%Y%m%d}.csv"
        )
        for symbol in symbols
    }
    if any(path.exists() or path.with_name(path.name + ".provenance.json").exists()
           for path in output_paths.values()):
        parser.error("an output CSV or provenance file already exists; choose a new range or directory")

    client = _load_safety_settings(parser)
    pages_by_symbol: dict[str, int] = {symbol: 0 for symbol in symbols}
    complete_by_symbol: dict[str, dict[date, list]] = {symbol: {} for symbol in symbols}
    try:
        for symbol in symbols:
            for session_date in closed_dates:
                try:
                    snapshot = client.get_domestic_minute_session_snapshot(symbol, session_date)
                except (KisApiError, OSError, ValueError) as exc:
                    code = getattr(exc, "broker_code", None)
                    parser.error(
                        f"KIS historical read failed for {symbol} {session_date.isoformat()} "
                        f"(broker code {code if code else 'unavailable'}); no export was started"
                    )
                pages_by_symbol[symbol] += snapshot.pages_fetched
                if _complete_session_bars(session_date, snapshot.bars):
                    complete_by_symbol[symbol][session_date] = snapshot.bars
    finally:
        client.close()

    shared_sessions = sorted(set.intersection(*(set(rows) for rows in complete_by_symbol.values())))
    if len(shared_sessions) < args.minimum_shared_sessions:
        parser.error(
            "complete common session count is below the configured minimum "
            f"({len(shared_sessions)}/{args.minimum_shared_sessions}); no export was written"
        )

    exports = []
    try:
        for symbol in symbols:
            rows = [
                (bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume,
                 None, None, KIS_REST_MINUTE_SOURCE)
                for session_date in shared_sessions
                for bar in complete_by_symbol[symbol][session_date]
            ]
            sidecar = write_minute_bar_export(
                output_paths[symbol], rows, market="KR", symbol=symbol,
                from_date=shared_sessions[0], to_date=shared_sessions[-1],
                sources=[KIS_REST_MINUTE_SOURCE], source_reference=SOURCE_REFERENCE,
            )
            exports.append({
                "symbol": symbol,
                "csv_path": str(output_paths[symbol].relative_to(repository)),
                "provenance_path": str(output_paths[symbol].with_name(
                    output_paths[symbol].name + ".provenance.json").relative_to(repository)),
                "session_count": len(shared_sessions),
                "bar_count": len(rows),
                "spread_observations": 0,
                "dataset_sha256": sidecar["dataset_sha256"],
                "rights_reviewed": sidecar["rights_reviewed"],
            })
    except (OSError, ValueError) as exc:
        # Every destination was confirmed absent before collection began. Include
        # the in-progress pair too, in case writing its sidecar failed after CSV replacement.
        for path in output_paths.values():
            path.unlink(missing_ok=True)
            path.with_name(path.name + ".provenance.json").unlink(missing_ok=True)
        parser.error(f"local KIS export could not be completed ({type(exc).__name__})")

    print(json.dumps({
        "market": "KR",
        "environment": "paper",
        "from_date": shared_sessions[0].isoformat(),
        "to_date": shared_sessions[-1].isoformat(),
        "shared_complete_sessions": [session.isoformat() for session in shared_sessions],
        "complete_sessions_by_symbol": {symbol: len(rows) for symbol, rows in complete_by_symbol.items()},
        "pages_fetched_by_symbol": pages_by_symbol,
        "exports": exports,
        "all_sessions_have_observed_spreads": False,
        "account_or_order_endpoints_called": False,
        "orders_sent": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
