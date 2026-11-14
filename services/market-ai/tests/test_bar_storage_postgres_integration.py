from __future__ import annotations

import asyncio
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app import snapshot_store
from app.paper_runtime import PaperSessionFeedMonitor, PaperWatch
from app.quant import MinuteBar


def _connection_args() -> dict[str, object]:
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
        "dbname": os.getenv("POSTGRES_DB", "ai_stock_trader"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
        "connect_timeout": 3,
    }


@pytest.fixture
def isolated_market_bar_schema(monkeypatch: pytest.MonkeyPatch) -> str:
    if os.getenv("RUN_LOCAL_POSTGRES_INTEGRATION_TESTS", "false").lower() != "true":
        pytest.skip("PostgreSQL recovery integration is opt-in")

    schema = f"bar_recovery_{uuid4().hex}"
    repository = Path(__file__).resolve().parents[3]
    migration_paths = (
        repository / "backend/trading-api/src/main/resources/db/migration/V13__market_minute_bars.sql",
        repository / "backend/trading-api/src/main/resources/db/migration/V26__market_minute_bar_spread_observation_time.sql",
    )

    with psycopg.connect(**_connection_args()) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        for migration_path in migration_paths:
            for statement in migration_path.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    connection.execute(statement)

    monkeypatch.setenv("PGOPTIONS", f"-c search_path={schema}")
    try:
        yield schema
    finally:
        monkeypatch.delenv("PGOPTIONS", raising=False)
        with psycopg.connect(**_connection_args()) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_postgres_outage_recovers_durable_bar_after_runtime_restart(
    isolated_market_bar_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    del isolated_market_bar_schema  # The fixture scopes all persistence SQL to a disposable schema.
    monkeypatch.setenv("AI_STOCK_TRADER_BAR_OUTBOX_PATH", str(tmp_path / "bar-outbox.sqlite3"))
    watch = PaperWatch("KR", "998731")
    bar_at = datetime.fromisoformat("2026-11-13T09:00:00+09:00")
    bar = MinuteBar(
        bar_at, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), 1000,
        Decimal("4.5"), bar_at.replace(second=30),
    )
    original_port = os.getenv("POSTGRES_PORT")
    runtime_before_restart = PaperSessionFeedMonitor()
    runtime_before_restart._bar_storage_queue = asyncio.Queue()
    runtime_before_restart._automation_queue = asyncio.Queue()
    runtime_before_restart._set_status(bar_storage_state="RUNNING")

    with monkeypatch.context() as outage_environment:
        outage_environment.setenv("POSTGRES_PORT", "1")

        async def queue_during_outage() -> None:
            worker = asyncio.create_task(runtime_before_restart._bar_storage_loop())
            runtime_before_restart._queue_bar_storage(watch, bar, {"signal": "BUY_CANDIDATE"}, {})
            await asyncio.wait_for(runtime_before_restart._bar_storage_queue.join(), timeout=30)
            worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await worker

        asyncio.run(queue_during_outage())

    assert runtime_before_restart._bar_write_outbox.count() == 1
    assert runtime_before_restart.status()["bar_storage_error"] == "POSTGRES_WRITE_FAILED"
    assert runtime_before_restart.status()["automation_state"] == "BLOCKED"
    assert runtime_before_restart._automation_queue.empty()

    if original_port is None:
        monkeypatch.delenv("POSTGRES_PORT", raising=False)
    else:
        monkeypatch.setenv("POSTGRES_PORT", original_port)
    runtime_after_restart = PaperSessionFeedMonitor()
    runtime_after_restart._bar_storage_queue = asyncio.Queue()
    runtime_after_restart._automation_queue = asyncio.Queue()

    async def restore_and_drain() -> None:
        assert await runtime_after_restart._restore_bar_write_outbox() == 1
        worker = asyncio.create_task(runtime_after_restart._bar_storage_loop())
        deadline = asyncio.get_running_loop().time() + 5
        while runtime_after_restart.status()["bar_storage_pending_count"] and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

    asyncio.run(restore_and_drain())

    assert runtime_after_restart._bar_write_outbox.count() == 0
    assert runtime_after_restart.status()["bar_storage_state"] == "RUNNING"
    assert runtime_after_restart.status()["bar_storage_pending_count"] == 0
    assert runtime_after_restart._automation_queue.empty()
    with psycopg.connect(**_connection_args()) as connection:
        row = connection.execute(
            """SELECT count(*), min(source), min(spread_bps), min(spread_observed_at)
                 FROM market_minute_bar WHERE market = 'KR' AND symbol = %s AND bar_at = %s""",
            (watch.symbol, bar.timestamp),
        ).fetchone()
    assert row == (1, "KIS_PAPER_WEBSOCKET", Decimal("4.50000000"), bar.spread_observed_at)


def test_websocket_bar_provenance_is_preserved_against_rest_backfill(
    isolated_market_bar_schema: str,
) -> None:
    del isolated_market_bar_schema  # The fixture scopes all persistence SQL to a disposable schema.
    observed_at = datetime.fromisoformat("2026-11-13T09:00:30+09:00")
    websocket_bar = MinuteBar(
        datetime.fromisoformat("2026-11-13T09:00:00+09:00"), Decimal("100"), Decimal("102"),
        Decimal("99"), Decimal("101"), 1000, None, None,
    )
    rest_backfill = MinuteBar(
        websocket_bar.timestamp, Decimal("90"), Decimal("92"), Decimal("89"), Decimal("91"), 900,
    )
    rest_bar = MinuteBar(
        datetime.fromisoformat("2026-11-13T09:01:00+09:00"), Decimal("200"), Decimal("202"),
        Decimal("199"), Decimal("201"), 2000,
    )
    websocket_upgrade = MinuteBar(
        rest_bar.timestamp, Decimal("201"), Decimal("203"), Decimal("200"), Decimal("202"), 2100,
        Decimal("3.5"), observed_at.replace(minute=1),
    )

    snapshot_store.store_minute_bars("KR", "998731", [websocket_bar], source="KIS_PAPER_WEBSOCKET")
    snapshot_store.store_minute_bars("KR", "998731", [rest_backfill], source="KIS_REST_MINUTE_CHART")
    snapshot_store.store_minute_bars("KR", "998731", [rest_bar], source="KIS_REST_MINUTE_CHART")
    snapshot_store.store_minute_bars("KR", "998731", [websocket_upgrade], source="KIS_PAPER_WEBSOCKET")

    with psycopg.connect(**_connection_args()) as connection:
        rows = connection.execute(
            """SELECT bar_at, close_price, volume, spread_bps, spread_observed_at, source
                 FROM market_minute_bar WHERE market = 'KR' AND symbol = '998731' ORDER BY bar_at""",
        ).fetchall()

    assert rows == [
        (websocket_bar.timestamp, Decimal("101.00000000"), 1000, None, None, "KIS_PAPER_WEBSOCKET"),
        (websocket_upgrade.timestamp, Decimal("202.00000000"), 2100, Decimal("3.50000000"),
         websocket_upgrade.spread_observed_at, "KIS_PAPER_WEBSOCKET"),
    ]
