from datetime import datetime
from decimal import Decimal

from app import snapshot_store
from app.quant import MinuteBar


class _FakeConnection:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def executemany(self, query, rows):
        self.calls.append((query, list(rows)))


def test_minute_bars_are_upserted_with_provenance_and_null_spread(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(snapshot_store, "_connect", lambda: connection)
    bar = MinuteBar(
        timestamp=datetime.fromisoformat("2026-11-05T09:00:00+09:00"), open=Decimal("100"),
        high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), volume=1000,
    )

    snapshot_store.store_minute_bars("KR", "005930", [bar], source="KIS_REST_MINUTE_CHART")

    query, rows = connection.calls[0]
    assert "ON CONFLICT (market, symbol, bar_at) DO UPDATE" in query
    assert rows == [("KR", "005930", bar.timestamp, bar.open, bar.high, bar.low, bar.close,
                     1000, None, "KIS_REST_MINUTE_CHART")]


def test_empty_minute_bar_batch_does_not_open_database(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_store, "_connect", lambda: (_ for _ in ()).throw(AssertionError("unexpected DB call")))

    snapshot_store.store_minute_bars("KR", "005930", [], source="KIS_REST_MINUTE_CHART")
