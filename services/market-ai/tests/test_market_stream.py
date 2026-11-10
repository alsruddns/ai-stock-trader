from datetime import timedelta
from decimal import Decimal

import pytest

from app.market_stream import (
    MinuteBarAggregator, OrderBookQuote, TradeTick, parse_domestic_orderbook_frame,
    parse_domestic_orderbook_frame_with_diagnostics, parse_trade_frame,
    parse_trade_frame_with_diagnostics,
)


def _frame(tr_id: str, width: int, values: dict[int, str]) -> str:
    fields = ["0"] * width
    for index, value in values.items():
        fields[index] = value
    return f"0|{tr_id}|1|" + "^".join(fields)


def test_parse_domestic_trade_frame_extracts_market_only_fields() -> None:
    frame = _frame("H0STCNT0", 47, {
        0: "005930", 1: "100101", 2: "73500", 7: "73000", 8: "74000", 9: "72500",
        10: "73510", 11: "73500", 12: "3", 13: "1000", 14: "73500000", 33: "20261105",
        45: "73500", 46: "2",
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
        fields = ["0"] * 47
        fields[0], fields[1], fields[2] = "005930", time_value, "73500"
        fields[10], fields[11] = "73510", "73500"
        fields[12], fields[13], fields[33] = trade_volume, cumulative_volume, "20261105"
        fields[45], fields[46] = "73500", "2"
        rows.extend(fields)
    ticks = parse_trade_frame("0|H0STCNT0|2|" + "^".join(rows), "KR")

    assert [tick.volume for tick in ticks] == [3, 5]


@pytest.mark.parametrize("market_class", ["1", "3", "5", ""])
def test_domestic_trade_parser_excludes_non_regular_market_ticks(market_class: str) -> None:
    frame = _frame("H0STCNT0", 47, {
        0: "005930", 1: "160101", 2: "73500", 10: "73510", 11: "73500",
        12: "3", 33: "20261105", 45: "2", 46: market_class,
    })

    assert parse_trade_frame(frame, "KR") == []


def test_domestic_trade_diagnostics_count_filtered_market_classes() -> None:
    frame = _frame("H0STCNT0", 47, {
        0: "005930", 1: "160101", 2: "73500", 10: "73510", 11: "73500",
        12: "3", 33: "20261105", 45: "2", 46: "3",
    })

    ticks, filtered = parse_trade_frame_with_diagnostics(frame, "KR")

    assert ticks == []
    assert filtered == 1


def test_domestic_trade_parser_uses_market_class_field_after_vi_reference_price() -> None:
    frame = _frame("H0STCNT0", 47, {
        0: "005930", 1: "100101", 2: "73500", 10: "73510", 11: "73500",
        12: "3", 33: "20261105", 45: "1", 46: "2",
    })

    assert len(parse_trade_frame(frame, "KR")) == 1


def test_domestic_trade_parser_rejects_legacy_short_row_without_market_class_field() -> None:
    frame = _frame("H0STCNT0", 46, {
        0: "005930", 1: "100101", 2: "73500", 10: "73510", 11: "73500",
        12: "3", 33: "20261105", 45: "2",
    })

    with pytest.raises(ValueError, match="all declared records"):
        parse_trade_frame(frame, "KR")


def test_parse_domestic_orderbook_frame_extracts_best_prices_from_official_columns() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    frame = _frame("H0STASP0", 63, {
        0: "005930", 1: "100101", 3: "73510", 13: "73500", 62: "2",
    })
    quotes = parse_domestic_orderbook_frame(
        frame, received_at=datetime(2026, 11, 6, 10, 1, 2, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert len(quotes) == 1
    assert quotes[0].symbol == "005930"
    assert quotes[0].timestamp.isoformat() == "2026-11-06T10:01:01+09:00"
    assert quotes[0].bid == Decimal("73500")
    assert quotes[0].ask == Decimal("73510")


@pytest.mark.parametrize("market_class", ["1", "3", "5", ""])
def test_domestic_orderbook_parser_excludes_non_regular_market_quotes(market_class: str) -> None:
    frame = _frame("H0STASP0", 63, {
        0: "005930", 1: "160101", 3: "73510", 13: "73500", 62: market_class,
    })

    assert parse_domestic_orderbook_frame(frame) == []


def test_domestic_orderbook_diagnostics_count_filtered_market_classes() -> None:
    frame = _frame("H0STASP0", 63, {
        0: "005930", 1: "160101", 3: "73510", 13: "73500", 62: "3",
    })

    quotes, filtered = parse_domestic_orderbook_frame_with_diagnostics(frame)

    assert quotes == []
    assert filtered == 1


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
    assert aggregate.pending_minute == first.timestamp.replace(second=0, microsecond=0)
    assert aggregate.add(same_minute) is None
    completed = aggregate.add(next_minute)
    assert completed is not None
    assert (completed.open, completed.high, completed.low, completed.close, completed.volume) == (
        Decimal("100"), Decimal("101"), Decimal("100"), Decimal("101"), 5,
    )
    with pytest.raises(ValueError, match="chronological"):
        aggregate.add(first)
    assert aggregate.flush().volume == 4
    assert aggregate.pending_minute is None


def test_minute_aggregator_uses_same_minute_orderbook_quote_for_spread() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Seoul")
    aggregate = MinuteBarAggregator()
    first = TradeTick("KR", "005930", datetime(2026, 11, 6, 10, 1, 1, tzinfo=zone), Decimal("100"), 2, None, None)
    next_minute = TradeTick("KR", "005930", datetime(2026, 11, 6, 10, 2, 1, tzinfo=zone), Decimal("101"), 3, None, None)
    aggregate.add(first)
    assert aggregate.update_quote(OrderBookQuote(
        "KR", "005930", datetime(2026, 11, 6, 10, 1, 30, tzinfo=zone), Decimal("99"), Decimal("101"),
    ))

    completed = aggregate.add(next_minute)

    assert completed is not None
    assert completed.spread_bps == Decimal("200")


def test_minute_aggregator_closes_prior_bar_before_later_minute_quote() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Seoul")
    aggregate = MinuteBarAggregator()
    tick = TradeTick("KR", "005930", datetime(2026, 11, 6, 10, 1, 1, tzinfo=zone),
                     Decimal("100"), 2, None, None)
    aggregate.add(tick)
    aggregate.update_quote(OrderBookQuote(
        "KR", "005930", datetime(2026, 11, 6, 10, 1, 30, tzinfo=zone), Decimal("99"), Decimal("101"),
    ))

    closed = aggregate.close_before(datetime(2026, 11, 6, 10, 2, 0, tzinfo=zone))

    assert closed is not None
    assert closed.timestamp == datetime(2026, 11, 6, 10, 1, tzinfo=zone)
    assert closed.spread_bps == Decimal("200")
    assert aggregate.pending_minute is None
    next_tick = TradeTick("KR", "005930", datetime(2026, 11, 6, 10, 2, 1, tzinfo=zone),
                          Decimal("101"), 3, None, None)
    assert aggregate.add(next_tick) is None
    assert aggregate.pending_minute == next_tick.timestamp.replace(second=0, microsecond=0)
    with pytest.raises(ValueError, match="chronological"):
        aggregate.add(tick)


def test_minute_aggregator_ignores_empty_trade_quote_after_orderbook_quote() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Seoul")
    aggregate = MinuteBarAggregator()
    first = TradeTick("KR", "005930", datetime(2026, 11, 6, 10, 1, 1, tzinfo=zone), Decimal("100"), 2, None, None)
    aggregate.add(first)
    assert aggregate.update_quote(OrderBookQuote(
        "KR", "005930", datetime(2026, 11, 6, 10, 1, 20, tzinfo=zone), Decimal("99"), Decimal("101"),
    ))

    # KIS trade frames can have empty best-bid/ask fields. They must not erase
    # the valid same-minute quote delivered by the separate orderbook stream.
    aggregate.add(TradeTick(
        "KR", "005930", datetime(2026, 11, 6, 10, 1, 40, tzinfo=zone), Decimal("100.5"), 1,
        Decimal("0"), Decimal("0"),
    ))
    completed = aggregate.add(TradeTick(
        "KR", "005930", datetime(2026, 11, 6, 10, 2, 1, tzinfo=zone), Decimal("101"), 3, None, None,
    ))

    assert completed is not None
    assert completed.spread_bps == Decimal("200")


def test_minute_aggregator_rejects_stale_orderbook_quote_for_spread() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Seoul")
    aggregate = MinuteBarAggregator()
    first = TradeTick("KR", "005930", datetime(2026, 11, 6, 10, 1, 40, tzinfo=zone), Decimal("100"), 2, None, None)
    assert aggregate.add(first) is None
    assert not aggregate.update_quote(OrderBookQuote(
        "KR", "005930", datetime(2026, 11, 6, 10, 0, 59, tzinfo=zone), Decimal("99"), Decimal("101"),
    ))

    completed = aggregate.add(TradeTick(
        "KR", "005930", datetime(2026, 11, 6, 10, 2, 1, tzinfo=zone), Decimal("101"), 3, None, None,
    ))

    assert completed is not None
    assert completed.spread_bps is None


@pytest.mark.parametrize(
    ("bid", "ask"),
    [(Decimal("0"), Decimal("101")), (Decimal("102"), Decimal("101")), (None, Decimal("101"))],
)
def test_minute_aggregator_keeps_last_valid_quote_after_bad_same_minute_update(
    bid: Decimal | None, ask: Decimal | None,
) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Seoul")
    aggregate = MinuteBarAggregator()
    aggregate.add(TradeTick(
        "KR", "005930", datetime(2026, 11, 6, 10, 1, 2, tzinfo=zone), Decimal("100"), 1, None, None,
    ))
    assert aggregate.update_quote(OrderBookQuote(
        "KR", "005930", datetime(2026, 11, 6, 10, 1, 10, tzinfo=zone), Decimal("99"), Decimal("101"),
    ))
    assert not aggregate.update_quote(OrderBookQuote(
        "KR", "005930", datetime(2026, 11, 6, 10, 1, 20, tzinfo=zone), bid, ask,
    ))

    completed = aggregate.add(TradeTick(
        "KR", "005930", datetime(2026, 11, 6, 10, 2, 1, tzinfo=zone), Decimal("100"), 1, None, None,
    ))

    assert completed is not None
    assert completed.spread_bps == Decimal("200")


def test_minute_aggregator_does_not_carry_valid_quote_into_minute_with_only_bad_updates() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Seoul")
    aggregate = MinuteBarAggregator()
    aggregate.add(TradeTick(
        "KR", "005930", datetime(2026, 11, 6, 10, 1, 2, tzinfo=zone), Decimal("100"), 1, None, None,
    ))
    aggregate.update_quote(OrderBookQuote(
        "KR", "005930", datetime(2026, 11, 6, 10, 1, 10, tzinfo=zone), Decimal("99"), Decimal("101"),
    ))
    aggregate.add(TradeTick(
        "KR", "005930", datetime(2026, 11, 6, 10, 2, 1, tzinfo=zone), Decimal("100"), 1, None, None,
    ))
    assert aggregate.update_quote(OrderBookQuote(
        "KR", "005930", datetime(2026, 11, 6, 10, 2, 10, tzinfo=zone), Decimal("0"), Decimal("101"),
    ))

    completed = aggregate.add(TradeTick(
        "KR", "005930", datetime(2026, 11, 6, 10, 3, 1, tzinfo=zone), Decimal("100"), 1, None, None,
    ))

    assert completed is not None
    assert completed.spread_bps is None
