from datetime import timedelta
from decimal import Decimal

import pytest

from app.market_stream import MinuteBarAggregator, TradeTick, parse_trade_frame


def _frame(tr_id: str, width: int, values: dict[int, str]) -> str:
    fields = ["0"] * width
    for index, value in values.items():
        fields[index] = value
    return f"0|{tr_id}|1|" + "^".join(fields)


def test_parse_domestic_trade_frame_extracts_market_only_fields() -> None:
    frame = _frame("H0STCNT0", 46, {
        0: "005930", 1: "100101", 2: "73500", 7: "73000", 8: "74000", 9: "72500",
        10: "73510", 11: "73500", 12: "3", 13: "1000", 14: "73500000", 33: "20261105",
    })
    tick = parse_trade_frame(frame, "KR")[0]
    assert tick.symbol == "005930"
    assert tick.timestamp.isoformat() == "2026-11-05T10:01:01+09:00"
    assert tick.price == Decimal("73500")
    assert tick.volume == 3
    assert tick.spread_bps is not None


def test_parse_domestic_multi_record_frame_uses_trade_volume_not_cumulative_volume() -> None:
    rows = []
    for time_value, trade_volume, cumulative_volume in (
        ("100101", "3", "1000"),
        ("100102", "5", "1005"),
    ):
        fields = ["0"] * 46
        fields[0], fields[1], fields[2] = "005930", time_value, "73500"
        fields[10], fields[11] = "73510", "73500"
        fields[12], fields[13], fields[33] = trade_volume, cumulative_volume, "20261105"
        rows.extend(fields)
    ticks = parse_trade_frame("0|H0STCNT0|2|" + "^".join(rows), "KR")

    assert [tick.volume for tick in ticks] == [3, 5]


def test_parse_overseas_trade_frame_uses_exchange_local_timestamp() -> None:
    frame = _frame("HDFSCNT0", 26, {
        0: "DNAS", 1: "AAPL", 2: "2", 3: "20261105", 4: "20261105", 5: "093001",
        8: "230", 9: "232", 10: "229", 11: "231.5", 15: "231.49", 16: "231.51",
        19: "4", 20: "1000", 21: "231500",
    })
    tick = parse_trade_frame(frame, "US", "NAS")[0]
    assert tick.symbol == "AAPL"
    assert tick.timestamp.isoformat() == "2026-11-05T09:30:01-05:00"
    assert tick.price == Decimal("231.5")
    assert tick.volume == 4


def test_encrypted_or_wrong_market_frames_are_rejected() -> None:
    with pytest.raises(ValueError, match="unencrypted"):
        parse_trade_frame("1|H0STCNT0|1|secret", "KR")
    with pytest.raises(ValueError, match="does not match"):
        parse_trade_frame("0|HDFSCNT0|1|x", "KR")


def test_minute_aggregator_emits_closed_bar_and_rejects_out_of_order_ticks() -> None:
    from datetime import datetime, timezone

    aggregate = MinuteBarAggregator()
    first = TradeTick("KR", "005930", datetime(2026, 11, 5, 1, 0, 1, tzinfo=timezone.utc), Decimal("100"), 2, Decimal("99.9"), Decimal("100.1"))
    same_minute = TradeTick("KR", "005930", first.timestamp + timedelta(seconds=40), Decimal("101"), 3, Decimal("100.9"), Decimal("101.1"))
    next_minute = TradeTick("KR", "005930", first.timestamp + timedelta(minutes=1), Decimal("100.5"), 4, Decimal("100.4"), Decimal("100.6"))
    assert aggregate.add(first) is None
    assert aggregate.add(same_minute) is None
    completed = aggregate.add(next_minute)
    assert completed is not None
    assert (completed.open, completed.high, completed.low, completed.close, completed.volume) == (
        Decimal("100"), Decimal("101"), Decimal("100"), Decimal("101"), 5,
    )
    with pytest.raises(ValueError, match="chronological"):
        aggregate.add(first)
    assert aggregate.flush().volume == 4
