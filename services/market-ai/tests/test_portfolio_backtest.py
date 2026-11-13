from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.portfolio_backtest import backtest_domestic_portfolio
from app.quant import MinuteBar, QuantConfig


def _bars(symbol_index: int = 0, session_date: int = 5) -> list[MinuteBar]:
    start = datetime(2026, 11, session_date, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    return [MinuteBar(start + timedelta(minutes=i), Decimal("100"), Decimal("101"),
                      Decimal("99"), Decimal("100"), 1000 + symbol_index, Decimal("5"),
                      start + timedelta(minutes=i, seconds=30)) for i in range(380)]


def _strategy_bars(symbol_index: int) -> list[MinuteBar]:
    start = datetime(2026, 11, 5, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    bars = [MinuteBar(start + timedelta(minutes=i), Decimal("100"), Decimal("100.5"), Decimal("99.5"),
                      Decimal("100"), 1000 + symbol_index, Decimal("5"),
                      start + timedelta(minutes=i, seconds=30)) for i in range(20)]
    bars.extend([
        MinuteBar(start + timedelta(minutes=20), Decimal("100.5"), Decimal("101.5"), Decimal("100.2"),
                  Decimal("101"), 3000, Decimal("5"), start + timedelta(minutes=20, seconds=30)),
        MinuteBar(start + timedelta(minutes=21), Decimal("102"), Decimal("103"), Decimal("101.5"),
                  Decimal("102.5"), 1800, Decimal("5"), start + timedelta(minutes=21, seconds=30)),
        MinuteBar(start + timedelta(minutes=22), Decimal("102.5"), Decimal("106"), Decimal("102"),
                  Decimal("105.5"), 2000, Decimal("5"), start + timedelta(minutes=22, seconds=30)),
    ])
    bars.extend(MinuteBar(start + timedelta(minutes=i), Decimal("100"), Decimal("100.5"), Decimal("99.5"),
                          Decimal("100"), 1000 + symbol_index, Decimal("5"),
                          start + timedelta(minutes=i, seconds=30)) for i in range(23, 380))
    return bars


def _run(bars_by_symbol, **overrides):
    config = overrides.pop("config", QuantConfig(commission_bps=Decimal(0), slippage_bps=Decimal(0)))
    return backtest_domestic_portfolio(
        bars_by_symbol, starting_equity=Decimal("1000000"), max_order_notional=Decimal("1000000"),
        config=config, **overrides,
    )


def _signals_at_lengths(monkeypatch, desired: dict[int, set[int]]) -> None:
    def candidate(prefix, _config, *, market):
        assert market == "KR"
        symbol_index = prefix[0].volume - 1000
        return len(prefix) in desired.get(symbol_index, set())

    monkeypatch.setattr("app.portfolio_backtest.orb_vwap_entry_candidate", candidate)


def test_portfolio_sizes_entries_under_risk_allocation_and_shared_cash(monkeypatch) -> None:
    _signals_at_lengths(monkeypatch, {0: {21}, 1: {21}})
    data = {"005930": _bars(0), "000660": _bars(1)}

    result = _run(data)

    assert result.signals_by_symbol == {"000660": 1, "005930": 1}
    assert len(result.trades) == 2
    assert all(800 <= trade.quantity <= 833 for trade in result.trades)
    assert sum(Decimal(trade.quantity) * Decimal("100") for trade in result.trades) <= Decimal("200000")
    assert result.maximum_open_positions == 2
    assert result.executable is False


def test_portfolio_generates_real_orb_vwap_signals_on_shared_timeline() -> None:
    data = {"005930": _strategy_bars(0), "000660": _strategy_bars(1)}
    config = QuantConfig(
        commission_bps=Decimal(0), slippage_bps=Decimal(0),
        minimum_turnover=Decimal(0), minimum_relative_volume=Decimal("1.5"),
    )

    result = backtest_domestic_portfolio(
        data, starting_equity=Decimal("1000000"), max_order_notional=Decimal("1000000"), config=config,
    )

    assert result.signals_by_symbol == {"000660": 1, "005930": 1}
    assert len(result.trades) == 2
    assert {trade.exit_reason for trade in result.trades} == {"TAKE_PROFIT"}
    assert all(trade.quantity > 0 for trade in result.trades)
    assert result.executable is False


def test_ai_filter_only_allows_quant_candidate_and_records_rejection() -> None:
    data = {"005930": _strategy_bars(0), "000660": _strategy_bars(1)}
    config = QuantConfig(
        commission_bps=Decimal(0), slippage_bps=Decimal(0),
        minimum_turnover=Decimal(0), minimum_relative_volume=Decimal("1.5"),
    )

    result = backtest_domestic_portfolio(
        data, starting_equity=Decimal("1000000"), max_order_notional=Decimal("1000000"),
        config=config, entry_filter=lambda symbol, _history: symbol == "005930",
    )

    assert result.quant_signals_by_symbol["000660"] > 0
    assert result.quant_signals_by_symbol["005930"] == 1
    assert result.signals_by_symbol == {"000660": 0, "005930": 1}
    assert result.ai_rejections_by_symbol["000660"] == result.quant_signals_by_symbol["000660"]
    assert result.ai_rejections_by_symbol["005930"] == 0
    assert [trade.symbol for trade in result.trades] == ["005930"]


def test_portfolio_enforces_three_slots_and_blocks_same_minute_slot_reuse(monkeypatch) -> None:
    _signals_at_lengths(monkeypatch, {0: {21}, 1: {21}, 2: {21}, 3: {21, 25}})
    data = {f"00000{index + 1}": _bars(index) for index in range(4)}

    result = _run(data)

    assert result.maximum_open_positions == 3
    assert len(result.trades) == 3
    assert result.signals_by_symbol["000004"] == 2
    assert result.rejected_entries == (("000004", "MAX_OPEN_POSITIONS"),
                                       ("000004", "MAX_OPEN_POSITIONS"))
    assert result.executable is False


def test_daily_loss_mark_blocks_new_signal_but_existing_position_is_managed(monkeypatch) -> None:
    _signals_at_lengths(monkeypatch, {0: {21}, 1: {24}})
    first_bars = _bars(0)
    stamp = first_bars[23].timestamp
    first_bars[23] = MinuteBar(stamp, Decimal("70"), Decimal("71"), Decimal("69"), Decimal("70"),
                               1000, Decimal("5"), stamp + timedelta(seconds=30))
    data = {"005930": first_bars, "000660": _bars(1)}

    result = _run(data)

    assert result.daily_loss_limit_hit is True
    assert result.rejected_entries == (("000660", "DAILY_LOSS_LIMIT"),)
    assert [trade.symbol for trade in result.trades] == ["005930"]
    assert result.trades[0].exit_reason == "ATR_STOP"
    assert result.executable is False


def test_three_consecutive_losses_stop_later_entries(monkeypatch) -> None:
    _signals_at_lengths(monkeypatch, {index: {21, 24} for index in range(3)})
    data = {f"00000{index + 1}": _bars(index) for index in range(3)}
    for bars in data.values():
        stamp = bars[22].timestamp
        bars[22] = MinuteBar(stamp, Decimal("100"), Decimal("101"), Decimal("95"), Decimal("96"),
                             1000, Decimal("5"), stamp + timedelta(seconds=30))

    result = _run(data)

    assert result.consecutive_loss_limit_hit is True
    assert len(result.trades) == 3
    assert result.rejected_entries == tuple((symbol, "CONSECUTIVE_LOSS_LIMIT")
                                            for symbol in sorted(data))
    assert result.daily_loss_limit_hit is False


def test_rejects_mismatched_session_timestamps_and_executable_market_data() -> None:
    first = _bars(0)
    mismatched = _bars(1, session_date=6)

    with pytest.raises(ValueError, match="same KR session"):
        _run({"005930": first, "000660": mismatched})
    with pytest.raises(ValueError, match="at least two"):
        _run({"005930": first})


def test_portfolio_rejects_non_domestic_ticker_symbols() -> None:
    with pytest.raises(ValueError, match="six-digit domestic ticker"):
        _run({"AAPL": _bars(0), "000660": _bars(1)})


def test_portfolio_uses_stop_first_when_entry_bar_touches_stop_and_target(monkeypatch) -> None:
    _signals_at_lengths(monkeypatch, {0: {21}})
    monkeypatch.setattr("app.portfolio_backtest._atr", lambda _bars, _period: Decimal("1"))
    first = _bars(0)
    stamp = first[21].timestamp
    first[21] = MinuteBar(stamp, Decimal("100"), Decimal("103"), Decimal("98"), Decimal("101"),
                          1000, Decimal("5"), stamp + timedelta(seconds=30))
    config = QuantConfig(commission_bps=Decimal(0), slippage_bps=Decimal(0),
                         atr_stop_multiple=Decimal("1"), reward_risk_multiple=Decimal("2"))

    result = _run({"005930": first, "000660": _bars(1)}, config=config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_time == stamp
    assert trade.exit_time == stamp
    assert trade.exit_reason == "ATR_STOP"
    assert trade.gross_pnl < 0
    assert trade.costs > 0
    assert trade.net_pnl < trade.gross_pnl


def test_portfolio_gap_through_stop_fills_at_worse_open(monkeypatch) -> None:
    _signals_at_lengths(monkeypatch, {0: {21}})
    monkeypatch.setattr("app.portfolio_backtest._atr", lambda _bars, _period: Decimal("1"))
    first = _bars(0)
    entry_stamp = first[21].timestamp
    first[21] = MinuteBar(entry_stamp, Decimal("100"), Decimal("100.5"), Decimal("99.5"), Decimal("100"),
                          1000, Decimal("5"), entry_stamp + timedelta(seconds=30))
    stamp = first[22].timestamp
    first[22] = MinuteBar(stamp, Decimal("90"), Decimal("91"), Decimal("89"), Decimal("90"),
                          1000, Decimal("5"), stamp + timedelta(seconds=30))
    config = QuantConfig(commission_bps=Decimal(0), slippage_bps=Decimal(0),
                         atr_stop_multiple=Decimal("1"), reward_risk_multiple=Decimal("2"))

    result = _run({"005930": first, "000660": _bars(1)}, config=config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_time == stamp
    assert trade.exit_reason == "ATR_STOP"
    assert trade.gross_pnl == -Decimal(10) * trade.quantity
    assert trade.costs > 0
    assert trade.net_pnl < trade.gross_pnl
