from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

from app.kis import KisReadOnlyClient, KisSettings
from app.quant import backtest_orb_vwap


def load_local_environment() -> None:
    env_file = repository / ".env_local"
    if not env_file.is_file():
        env_file = repository / ".env"
    if not env_file.is_file():
        raise SystemExit("Repository .env_local or .env file is missing.")
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[name.strip()] = value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a read-only KIS PAPER KRX session-bar and non-executable backtest smoke test."
    )
    parser.add_argument("--session-date", type=date.fromisoformat, required=True)
    parser.add_argument("--symbol", default="005930")
    parser.add_argument("--end-time", default="153000")
    args = parser.parse_args()

    load_local_environment()
    required_locks = {
        "TRADING_MODE": "paper",
        "LIVE_TRADING_ENABLED": "false",
        "KIS_PAPER_ORDERS_ENABLED": "false",
        "KIS_REAL_ORDERS_ENABLED": "false",
        "KIS_REAL_RECONCILIATION_ENABLED": "false",
        "PAPER_AUTOTRADING_ENABLED": "false",
    }
    for name, required in required_locks.items():
        if os.getenv(name, "false").strip().lower() != required:
            raise SystemExit(f"{name} must remain {required} for this read-only smoke test.")
    if os.getenv("KIS_ENVIRONMENT", "paper").strip().lower() != "paper":
        raise SystemExit("KIS_ENVIRONMENT must be paper.")
    if os.getenv("KIS_MARKET_DATA_ENABLED", "false").strip().lower() != "true":
        raise SystemExit("KIS_MARKET_DATA_ENABLED must be true for this explicit read-only test.")

    settings = KisSettings.from_environment("paper")
    if not settings.app_key or not settings.app_secret:
        raise SystemExit("PAPER KIS key and secret must be configured in .env_local.")
    client = KisReadOnlyClient(settings)
    try:
        bars = client.get_domestic_minute_session_bars(args.symbol, args.session_date, args.end_time)
    finally:
        client.close()
    if not bars:
        raise SystemExit("KIS returned no minute bars for the requested session.")
    result = backtest_orb_vwap("KR", args.symbol, bars)
    if result.executable or any(bar.spread_bps is not None for bar in bars):
        raise SystemExit("Unexpected executable result or spread data in the KIS historical contract.")

    print(json.dumps({
        "status": "ok",
        "environment": "paper",
        "market": "KR",
        "symbol": args.symbol,
        "session": result.session.isoformat(),
        "bar_count": len(bars),
        "first_bar_at": bars[0].timestamp.isoformat(),
        "last_bar_at": bars[-1].timestamp.isoformat(),
        "spread_observations": 0,
        "signals": result.signals,
        "trades": len(result.trades),
        "net_pnl": str(result.net_pnl),
        "executable": result.executable,
        "persisted": False,
        "orders_sent": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
