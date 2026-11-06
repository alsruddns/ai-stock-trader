from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.quant import MinuteBar, QuantConfig, backtest_orb_vwap, orb_vwap_entry_candidate


def _session_bars(bar_count: int = 380) -> list[MinuteBar]:
    start = datetime(2026, 11, 5, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    bars = [
        MinuteBar(start + timedelta(minutes=i), Decimal("100"), Decimal("100.5"), Decimal("99.5"), Decimal("100"), 1000, Decimal("5"))
        for i in range(20)
    ]
    bars.append(MinuteBar(start + timedelta(minutes=20), Decimal("100.5"), Decimal("101.5"), Decimal("100.2"), Decimal("101"), 3000, Decimal("5")))
    bars.append(MinuteBar(start + timedelta(minutes=21), Decimal("102"), Decimal("103"), Decimal("101.5"), Decimal("102.5"), 1800, Decimal("5")))
    bars.append(MinuteBar(start + timedelta(minutes=22), Decimal("102.5"), Decimal("106"), Decimal("102"), Decimal("105.5"), 2000, Decimal("5")))
    bars.extend(
        MinuteBar(start + timedelta(minutes=i), Decimal("100"), Decimal("100.5"), Decimal("99.5"),
                  Decimal("100"), 1000, Decimal("5"))
        for i in range(23, bar_count)
    )
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


def test_observed_spread_reduces_entry_and_exit_fill_prices() -> None:
    bars_with_spread = _session_bars()
    bars_without_spread = [
        MinuteBar(bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume, Decimal("0"))
        for bar in bars_with_spread
    ]
    config = QuantConfig(
        commission_bps=Decimal("0"), slippage_bps=Decimal("0"),
        minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1"),
    )

    spread_trade = backtest_orb_vwap("KR", "005930", bars_with_spread, config).trades[0]
    zero_spread_trade = backtest_orb_vwap("KR", "005930", bars_without_spread, config).trades[0]

    assert spread_trade.entry_price > zero_spread_trade.entry_price
    assert spread_trade.exit_price < zero_spread_trade.exit_price
    assert spread_trade.net_pnl < zero_spread_trade.net_pnl
    assert spread_trade.costs > zero_spread_trade.costs
    assert spread_trade.net_pnl == spread_trade.gross_pnl - spread_trade.costs
    assert zero_spread_trade.costs == 0


def test_entry_with_missing_or_over_limit_next_bar_spread_is_not_filled() -> None:
    for next_spread in (None, Decimal("16")):
        bars = _session_bars()
        next_bar = bars[21]
        bars[21] = MinuteBar(next_bar.timestamp, next_bar.open, next_bar.high, next_bar.low,
                             next_bar.close, next_bar.volume, next_spread)
        following = bars[22]
        bars[22] = MinuteBar(following.timestamp, following.open, following.high, following.low,
                             following.close, following.volume, Decimal("16"))
        result = backtest_orb_vwap(
            "KR", "005930", bars,
            QuantConfig(minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1")),
        )
        assert result.signals >= 1
        assert result.trades == ()


def test_missing_exit_spread_uses_configured_spread_ceiling() -> None:
    bars = _session_bars()
    for index in range(22, len(bars)):
        prior = bars[index]
        bars[index] = MinuteBar(prior.timestamp, Decimal("102"), Decimal("104"), Decimal("101"),
                                Decimal("102"), prior.volume, prior.spread_bps)
    last = bars[-1]
    bars[-1] = MinuteBar(last.timestamp, last.open, last.high, last.low, last.close, last.volume, None)
    config = QuantConfig(
        commission_bps=Decimal("0"), slippage_bps=Decimal("0"),
        minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1"),
        maximum_spread_bps=Decimal("15"),
    )

    result = backtest_orb_vwap("KR", "005930", bars, config)

    assert len(result.trades) == 1
    assert result.trades[0].exit_price < bars[-1].close


def test_optional_ai_entry_filter_runs_only_after_quant_candidate_without_future_bars(monkeypatch) -> None:
    bars = _session_bars()
    seen_histories = []
    monkeypatch.setattr("app.quant.orb_vwap_entry_candidate", lambda history, *_args, **_kwargs: len(history) == 21)

    def reject_candidate(history):
        seen_histories.append(tuple(item.timestamp for item in history))
        return False

    config = QuantConfig(minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1"))
    result = backtest_orb_vwap("KR", "005930", bars, config, entry_filter=reject_candidate)

    assert result.signals == 0
    assert result.trades == ()
    assert len(seen_histories) == 1
    assert seen_histories[0][-1] == bars[20].timestamp
    assert bars[21].timestamp not in seen_histories[0]


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

    assert orb_vwap_entry_candidate(bars, config, market="KR") is False
    next_bar_time = start + timedelta(minutes=1)
    bars.append(MinuteBar(next_bar_time, Decimal("101"), Decimal("101.5"), Decimal("100.8"),
                          Decimal("101.2"), 3000, Decimal("5")))
    assert orb_vwap_entry_candidate(bars, config, market="KR") is True


def test_stop_wins_when_stop_and_target_are_touched_in_same_bar() -> None:
    bars = _session_bars()
    ambiguous = MinuteBar(bars[21].timestamp, Decimal("102"), Decimal("110"), Decimal("90"), Decimal("100"), 2000, Decimal("5"))
    bars[21] = ambiguous
    result = backtest_orb_vwap("KR", "005930", bars, QuantConfig(minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1"), slippage_bps=Decimal("0"), commission_bps=Decimal("0")))
    assert result.trades[0].exit_reason == "ATR_STOP"


def test_us_backtest_uses_new_york_session_date_across_korea_midnight() -> None:
    bars = _session_bars(bar_count=390)
    start = datetime(2026, 11, 5, 23, 30, tzinfo=timezone(timedelta(hours=9)))
    bars = [MinuteBar(start + timedelta(minutes=index), bar.open, bar.high, bar.low,
                      bar.close, bar.volume, bar.spread_bps)
            for index, bar in enumerate(bars)]

    result = backtest_orb_vwap(
        "US", "AAPL", bars,
        QuantConfig(minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1"),
                    slippage_bps=Decimal("0"), commission_bps=Decimal("0")),
    )

    assert result.session.isoformat() == "2026-11-05"
    assert len(result.trades) == 1


def test_backtest_rejects_premarket_partial_open_and_missing_minute_bars() -> None:
    bars = _session_bars()
    premarket = datetime(2026, 11, 5, 23, 0, tzinfo=timezone(timedelta(hours=9)))
    us_bars = [MinuteBar(premarket + timedelta(minutes=i), bar.open, bar.high, bar.low,
                         bar.close, bar.volume, bar.spread_bps) for i, bar in enumerate(bars)]
    with pytest.raises(ValueError, match="official regular-session open"):
        backtest_orb_vwap("US", "AAPL", us_bars)

    partial_open = [MinuteBar(bar.timestamp + timedelta(minutes=1), bar.open, bar.high, bar.low,
                              bar.close, bar.volume, bar.spread_bps) for bar in bars]
    with pytest.raises(ValueError, match="official regular-session open"):
        backtest_orb_vwap("KR", "005930", partial_open)

    gap = bars[:10] + bars[11:]
    with pytest.raises(ValueError, match="without gaps"):
        backtest_orb_vwap("KR", "005930", gap)

    with pytest.raises(ValueError, match="final regular-session"):
        backtest_orb_vwap("KR", "005930", bars[:23])
