from datetime import datetime
from decimal import Decimal

from app import snapshot_store
from app.quant import MinuteBar


class _FakeConnection:
    def __init__(self):
        self.calls = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self

    def executemany(self, query, rows):
        self.calls.append((query, list(rows)))

    def execute(self, query, params):
        self.calls.append((query, params))

    def fetchall(self):
        return self.rows


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
    assert "WHERE market_minute_bar.spread_bps IS NULL OR EXCLUDED.spread_bps IS NOT NULL" in query
    assert rows == [("KR", "005930", bar.timestamp, bar.open, bar.high, bar.low, bar.close,
                     1000, None, "KIS_REST_MINUTE_CHART")]


def test_minute_bar_persistence_drops_premarket_and_afterhours(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(snapshot_store, "_connect", lambda: connection)
    before_open = MinuteBar(
        timestamp=datetime.fromisoformat("2026-11-05T08:59:00+09:00"), open=Decimal("100"),
        high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), volume=1000,
    )
    at_open = MinuteBar(
        timestamp=datetime.fromisoformat("2026-11-05T09:00:00+09:00"), open=Decimal("100"),
        high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), volume=1000,
    )
    closing_auction = MinuteBar(
        timestamp=datetime.fromisoformat("2026-11-05T15:25:00+09:00"), open=Decimal("100"),
        high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), volume=1000,
    )
    after_close = MinuteBar(
        timestamp=datetime.fromisoformat("2026-11-05T15:30:00+09:00"), open=Decimal("100"),
        high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"), volume=1000,
    )

    snapshot_store.store_minute_bars("KR", "005930", [before_open, at_open, closing_auction, after_close],
                                     source="KIS_PAPER_WEBSOCKET")

    _, rows = connection.calls[0]
    assert len(rows) == 1
    assert rows[0][2] == at_open.timestamp


def test_empty_minute_bar_batch_does_not_open_database(monkeypatch) -> None:
    monkeypatch.setattr(snapshot_store, "_connect", lambda: (_ for _ in ()).throw(AssertionError("unexpected DB call")))

    snapshot_store.store_minute_bars("KR", "005930", [], source="KIS_REST_MINUTE_CHART")


def test_session_bar_restore_uses_local_exchange_date_and_returns_ascending(monkeypatch) -> None:
    connection = _FakeConnection()
    newer = (datetime.fromisoformat("2026-11-05T09:01:00-05:00"), Decimal("101"), Decimal("102"),
             Decimal("100"), Decimal("101.5"), 1200, None)
    older = (datetime.fromisoformat("2026-11-05T09:00:00-05:00"), Decimal("100"), Decimal("101"),
             Decimal("99"), Decimal("100.5"), 1000, Decimal("4.5"))
    connection.rows = [newer, older]
    monkeypatch.setattr(snapshot_store, "_connect", lambda: connection)

    bars = snapshot_store.load_session_minute_bars("US", "aapl", datetime.fromisoformat("2026-11-05").date())

    assert connection.calls[0] == ("SET TRANSACTION READ ONLY", None)
    query, params = connection.calls[1]
    assert "bar_at >= %s AND bar_at < %s" in query
    assert params[0:2] == ("US", "AAPL")
    assert params[2].isoformat() == "2026-11-05T14:30:00+00:00"
    assert params[3].isoformat() == "2026-11-05T21:00:00+00:00"
    assert params[4] == 5000
    assert [bar.timestamp.isoformat() for bar in bars] == [
        "2026-11-05T09:00:00-05:00", "2026-11-05T09:01:00-05:00",
    ]
    assert bars[0].spread_bps == Decimal("4.5")


def test_multi_symbol_session_load_uses_single_read_only_query(monkeypatch) -> None:
    connection = _FakeConnection()
    connection.rows = [
        ("AAPL", datetime.fromisoformat("2026-11-05T09:30:00-05:00"), Decimal("100"), Decimal("101"),
         Decimal("99"), Decimal("100.5"), 1000, Decimal("4.5")),
        ("MSFT", datetime.fromisoformat("2026-11-05T09:30:00-05:00"), Decimal("200"), Decimal("201"),
         Decimal("199"), Decimal("200.5"), 500, None),
    ]
    monkeypatch.setattr(snapshot_store, "_connect", lambda: connection)

    result = snapshot_store.load_session_minute_bars_for_symbols(
        "US", ["aapl", "msft", "AAPL"], datetime.fromisoformat("2026-11-05").date(),
    )

    assert connection.calls[0] == ("SET TRANSACTION READ ONLY", None)
    query, params = connection.calls[1]
    assert "symbol = ANY(%s)" in query
    assert params[0:2] == ("US", ["AAPL", "MSFT"])
    assert list(result) == ["AAPL", "MSFT"]
    assert result["AAPL"][0].spread_bps == Decimal("4.5")
    assert result["MSFT"][0].spread_bps is None


def test_multi_symbol_source_counts_are_read_only_and_grouped_by_symbol(monkeypatch) -> None:
    connection = _FakeConnection()
    connection.rows = [
        ("AAPL", "KIS_REAL_US_WEBSOCKET", 390),
        ("AAPL", "KIS_REST_MINUTE_CHART", 2),
        ("MSFT", "KIS_REAL_US_WEBSOCKET", 390),
    ]
    monkeypatch.setattr(snapshot_store, "_connect", lambda: connection)

    result = snapshot_store.load_session_minute_bar_source_counts(
        "US", ["aapl", "msft", "AAPL"], datetime.fromisoformat("2026-11-05").date(),
    )

    assert connection.calls[0] == ("SET TRANSACTION READ ONLY", None)
    query, params = connection.calls[1]
    assert "GROUP BY symbol, source" in query
    assert params[0:2] == ("US", ["AAPL", "MSFT"])
    assert result == {
        "AAPL": {"KIS_REAL_US_WEBSOCKET": 390, "KIS_REST_MINUTE_CHART": 2},
        "MSFT": {"KIS_REAL_US_WEBSOCKET": 390},
    }
