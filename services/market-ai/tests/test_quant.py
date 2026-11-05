from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.quant import MinuteBar, QuantConfig, backtest_orb_vwap


def _session_bars() -> list[MinuteBar]:
    start = datetime(2026, 11, 5, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    bars = [
        MinuteBar(start + timedelta(minutes=i), Decimal("100"), Decimal("100.5"), Decimal("99.5"), Decimal("100"), 1000, Decimal("5"))
        for i in range(20)
    ]
    bars.append(MinuteBar(start + timedelta(minutes=20), Decimal("100.5"), Decimal("101.5"), Decimal("100.2"), Decimal("101"), 3000, Decimal("5")))
    bars.append(MinuteBar(start + timedelta(minutes=21), Decimal("102"), Decimal("103"), Decimal("101.5"), Decimal("102.5"), 1800, Decimal("5")))
    bars.append(MinuteBar(start + timedelta(minutes=22), Decimal("102.5"), Decimal("106"), Decimal("102"), Decimal("105.5"), 2000, Decimal("5")))
    return bars


def test_orb_vwap_enters_on_next_bar_and_applies_costs() -> None:
    config = QuantConfig(
        commission_bps=Decimal("5"), slippage_bps=Decimal("5"),
        minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1.5"),
    )
    result = backtest_orb_vwap("KR", "005930", _session_bars(), config)
    assert result.signals == 1
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_time == _session_bars()[21].timestamp
    assert trade.exit_reason == "TAKE_PROFIT"
    assert trade.costs > 0
    assert trade.net_pnl == trade.gross_pnl - trade.costs
    assert result.executable is False


def test_strategy_rejects_unsorted_or_cross_session_bars() -> None:
    bars = _session_bars()
    with pytest.raises(ValueError, match="strictly increasing"):
        backtest_orb_vwap("KR", "005930", [bars[1], bars[0]])
    next_day = MinuteBar(bars[-1].timestamp + timedelta(days=1), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), 1000)
    with pytest.raises(ValueError, match="one session"):
        backtest_orb_vwap("KR", "005930", bars[:2] + [next_day])


def test_strategy_rejects_naive_timestamp_and_bad_ohlc() -> None:
    with pytest.raises(ValueError, match="timezone"):
        MinuteBar(datetime(2026, 11, 5, 9, 0), *(Decimal("100"),) * 4, 100)
    with pytest.raises(ValueError, match="high/low"):
        MinuteBar(datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc), Decimal("100"), Decimal("99"), Decimal("98"), Decimal("100"), 1)


def test_missing_spread_and_low_turnover_block_entries() -> None:
    bars = _session_bars()
    breakout = bars[20]
    bars[20] = MinuteBar(breakout.timestamp, breakout.open, breakout.high, breakout.low, breakout.close, breakout.volume, None)
    result = backtest_orb_vwap("KR", "005930", bars, QuantConfig(minimum_turnover=Decimal("999999999999")))
    assert result.signals == 0
    assert result.trades == ()


def test_relative_volume_requires_full_prior_lookback_plus_current_bar() -> None:
    bars = _session_bars()[:20]
    start = bars[-1].timestamp
    bars[-1] = MinuteBar(start, Decimal("100.5"), Decimal("101.5"), Decimal("100.2"),
                         Decimal("101"), 3000, Decimal("5"))
    config = QuantConfig(minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1"))

    assert backtest_orb_vwap("KR", "005930", bars, config).signals == 0
    next_bar_time = start + timedelta(minutes=1)
    bars.append(MinuteBar(next_bar_time, Decimal("101"), Decimal("101.5"), Decimal("100.8"),
                          Decimal("101.2"), 3000, Decimal("5")))
    assert backtest_orb_vwap("KR", "005930", bars, config).signals == 1


def test_stop_wins_when_stop_and_target_are_touched_in_same_bar() -> None:
    bars = _session_bars()
    ambiguous = MinuteBar(bars[21].timestamp, Decimal("102"), Decimal("110"), Decimal("90"), Decimal("100"), 2000, Decimal("5"))
    bars[21] = ambiguous
    result = backtest_orb_vwap("KR", "005930", bars, QuantConfig(minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1"), slippage_bps=Decimal("0"), commission_bps=Decimal("0")))
    assert result.trades[0].exit_reason == "ATR_STOP"
