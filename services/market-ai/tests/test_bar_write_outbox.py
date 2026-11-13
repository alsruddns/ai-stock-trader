from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.bar_write_outbox import BarWriteOutbox, BarWriteOutboxFull
from app.quant import MinuteBar


def _bar(timestamp: datetime) -> MinuteBar:
    return MinuteBar(
        timestamp, Decimal("100.10"), Decimal("101.20"), Decimal("99.90"), Decimal("100.80"),
        1234, Decimal("4.25"), timestamp.replace(second=59),
    )


def test_outbox_survives_reopen_and_preserves_bar_provenance(tmp_path) -> None:
    path = tmp_path / "pending-bars.sqlite3"
    timestamp = datetime.fromisoformat("2026-11-13T09:00:00+09:00")
    first = BarWriteOutbox(path)
    assert first.enqueue("KR", "005930", _bar(timestamp), source="KIS_PAPER_WEBSOCKET") == 1

    reopened = BarWriteOutbox(path)
    pending = reopened.pending()

    assert len(pending) == 1
    market, symbol, bar, source = pending[0]
    assert (market, symbol, source) == ("KR", "005930", "KIS_PAPER_WEBSOCKET")
    assert bar.timestamp == timestamp.astimezone(timezone.utc)
    assert bar.open == Decimal("100.10")
    assert bar.close == Decimal("100.80")
    assert bar.volume == 1234
    assert bar.spread_bps == Decimal("4.25")
    assert bar.spread_observed_at == timestamp.replace(second=59)
    assert reopened.acknowledge(market, symbol, bar.timestamp) == 0


def test_outbox_deduplicates_equivalent_utc_timestamps(tmp_path) -> None:
    outbox = BarWriteOutbox(tmp_path / "pending-bars.sqlite3")
    timestamp = datetime.fromisoformat("2026-11-13T09:00:00+09:00")

    assert outbox.enqueue("KR", "005930", _bar(timestamp), source="KIS_PAPER_WEBSOCKET") == 1
    assert outbox.enqueue("KR", "005930", _bar(timestamp.astimezone(timezone.utc)),
                          source="KIS_PAPER_WEBSOCKET") == 1
    assert outbox.count() == 1


def test_outbox_capacity_fails_closed_without_dropping_existing_bar(tmp_path) -> None:
    outbox = BarWriteOutbox(tmp_path / "pending-bars.sqlite3", maximum_pending=1)
    first = datetime.fromisoformat("2026-11-13T09:00:00+09:00")
    second = first.replace(minute=1)
    outbox.enqueue("KR", "005930", _bar(first), source="KIS_PAPER_WEBSOCKET")

    with pytest.raises(BarWriteOutboxFull):
        outbox.enqueue("KR", "005930", _bar(second), source="KIS_PAPER_WEBSOCKET")

    assert outbox.count() == 1
    assert outbox.pending()[0][2].timestamp == first.astimezone(timezone.utc)
