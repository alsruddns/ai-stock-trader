from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.quant import MinuteBar, QuantConfig, _ema, backtest_orb_vwap, orb_vwap_entry_candidate


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


def test_spread_observation_time_must_be_timezone_aware_and_within_bar_minute() -> None:
    timestamp = datetime(2026, 11, 5, 9, 0, tzinfo=timezone(timedelta(hours=9)))

    with pytest.raises(ValueError, match="timezone"):
        MinuteBar(timestamp, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"),
                  1000, Decimal("5"), datetime(2026, 11, 5, 9, 0, 15))
    with pytest.raises(ValueError, match="bar minute"):
        MinuteBar(timestamp, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"),
                  1000, Decimal("5"), timestamp + timedelta(minutes=1))
    with pytest.raises(ValueError, match="requires a spread"):
        MinuteBar(timestamp, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"),
                  1000, None, timestamp + timedelta(seconds=15))


def test_ema_uses_initial_period_sma_then_all_subsequent_values() -> None:
    assert _ema([Decimal("10"), Decimal("11"), Decimal("12"), Decimal("13")], 3) == Decimal("12")
    with pytest.raises(ValueError, match="at least one full period"):
        _ema([Decimal("10"), Decimal("11")], 3)


def test_entry_candidate_ema_uses_full_session_prefix(monkeypatch) -> None:
    bars = _session_bars()[:21]
    observed_lengths = []

    def record_ema(values, period):
        observed_lengths.append(len(values))
        return Decimal("100")

    monkeypatch.setattr("app.quant._ema", record_ema)

    assert orb_vwap_entry_candidate(
        bars, QuantConfig(minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1")), market="KR",
    ) is True
    assert observed_lengths == [21]


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


def test_backtest_applies_direction_specific_commissions_and_sell_tax() -> None:
    bars = [MinuteBar(bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume, Decimal("0"))
            for bar in _session_bars()]
    config = QuantConfig(
        commission_bps=Decimal("0"), buy_commission_bps=Decimal("2"),
        sell_commission_bps=Decimal("3"), sell_tax_bps=Decimal("4"),
        slippage_bps=Decimal("0"), minimum_turnover=Decimal("0"),
        minimum_relative_volume=Decimal("1.5"),
    )

    result = backtest_orb_vwap("KR", "005930", bars, config)

    trade = result.trades[0]
    expected_fee = (trade.entry_price * Decimal("2") / Decimal("10000")
                    + trade.exit_price * Decimal("7") / Decimal("10000"))
    assert trade.costs == expected_fee
    assert config.minimum_round_trip_cost_bps == Decimal("9")


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


def test_next_bar_spread_does_not_look_ahead_into_entry_fill() -> None:
    for next_spread in (None, Decimal("16")):
        bars = _session_bars()
        next_bar = bars[21]
        bars[21] = MinuteBar(next_bar.timestamp, next_bar.open, next_bar.high, next_bar.low,
                             next_bar.close, next_bar.volume, next_spread)
        result = backtest_orb_vwap(
            "KR", "005930", bars,
            QuantConfig(minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1")),
        )
        assert len(result.trades) == 1
        expected_entry = bars[21].open * (Decimal(1) + (Decimal("5") + Decimal("2.5")) / Decimal("10000"))
        assert result.trades[0].entry_price == expected_entry


def test_intrabar_exit_cost_uses_preceding_quote_not_exit_bar_future_spread() -> None:
    baseline = _session_bars()
    changed = list(baseline)
    target_bar = changed[22]
    changed[22] = MinuteBar(target_bar.timestamp, target_bar.open, target_bar.high, target_bar.low,
                            target_bar.close, target_bar.volume, Decimal("100"))
    config = QuantConfig(minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1"))

    baseline_trade = backtest_orb_vwap("KR", "005930", baseline, config).trades[0]
    changed_trade = backtest_orb_vwap("KR", "005930", changed, config).trades[0]

    assert baseline_trade.exit_reason == changed_trade.exit_reason == "TAKE_PROFIT"
    assert changed_trade.exit_price == baseline_trade.exit_price
    assert changed_trade.costs == baseline_trade.costs


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


def test_vwap_close_signal_exits_at_next_bar_open_not_same_close(monkeypatch) -> None:
    bars = _session_bars()
    failing = bars[22]
    bars[22] = MinuteBar(failing.timestamp, Decimal("102.5"), Decimal("103"), Decimal("100.8"),
                         Decimal("101"), failing.volume, Decimal("0"))
    next_bar = bars[23]
    bars[23] = MinuteBar(next_bar.timestamp, Decimal("101.7"), Decimal("102"), Decimal("101.5"),
                         Decimal("101.8"), next_bar.volume, Decimal("0"))
    monkeypatch.setattr(
        "app.quant._vwap",
        lambda history: Decimal("102.5") if len(history) >= 22 else Decimal("100"),
    )
    config = QuantConfig(
        minimum_turnover=Decimal("0"), minimum_relative_volume=Decimal("1"),
        slippage_bps=Decimal("0"), commission_bps=Decimal("0"),
    )

    result = backtest_orb_vwap("KR", "005930", bars, config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "VWAP_LOSS"
    assert trade.exit_time == bars[23].timestamp
    assert trade.exit_price == bars[23].open


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


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_minute_bar_rejects_non_finite_ohlc(field: str) -> None:
    values = {
        "open": Decimal("100"), "high": Decimal("101"),
        "low": Decimal("99"), "close": Decimal("100"),
    }
    values[field] = Decimal("Infinity")

    with pytest.raises(ValueError, match="finite decimals"):
        MinuteBar(datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc), **values, volume=100)


def test_minute_bar_rejects_non_finite_spread_and_non_integer_volume() -> None:
    timestamp = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="finite decimal"):
        MinuteBar(timestamp, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), 100,
                  Decimal("Infinity"))
    with pytest.raises(ValueError, match="non-negative integer"):
        MinuteBar(timestamp, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), True)


@pytest.mark.parametrize("observed_at", [
    datetime(2026, 11, 5, 8, 59, 59, tzinfo=timezone(timedelta(hours=9))),
    datetime(2026, 11, 5, 9, 1, 0, tzinfo=timezone(timedelta(hours=9))),
])
def test_minute_bar_rejects_spread_observation_outside_bar_interval(observed_at: datetime) -> None:
    timestamp = datetime(2026, 11, 5, 9, 0, tzinfo=timezone(timedelta(hours=9)))

    with pytest.raises(ValueError, match="within its bar minute"):
        MinuteBar(timestamp, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"),
                  100, Decimal("5"), observed_at)


def test_minute_bar_accepts_spread_observation_at_last_second_of_bar() -> None:
    timestamp = datetime(2026, 11, 5, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    observed_at = datetime(2026, 11, 5, 0, 0, 59, 999999, tzinfo=timezone.utc)

    bar = MinuteBar(timestamp, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"),
                    100, Decimal("5"), observed_at)

    assert bar.spread_observed_at == observed_at


@pytest.mark.parametrize("field", ["minimum_turnover", "commission_bps", "buy_commission_bps",
                                    "sell_commission_bps", "sell_tax_bps", "slippage_bps"])
def test_quant_config_rejects_non_finite_numeric_settings(field: str) -> None:
    with pytest.raises(ValueError, match="finite decimals"):
        QuantConfig(**{field: Decimal("Infinity")})


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


def test_kr_backtest_rejects_partial_open_and_missing_minute_bars() -> None:
    bars = _session_bars()
    partial_open = [MinuteBar(bar.timestamp + timedelta(minutes=1), bar.open, bar.high, bar.low,
                              bar.close, bar.volume, bar.spread_bps) for bar in bars]
    with pytest.raises(ValueError, match="official regular-session open"):
        backtest_orb_vwap("KR", "005930", partial_open)

    gap = bars[:10] + bars[11:]
    with pytest.raises(ValueError, match="without gaps"):
        backtest_orb_vwap("KR", "005930", gap)

    with pytest.raises(ValueError, match="final regular-session"):
        backtest_orb_vwap("KR", "005930", bars[:23])


def test_us_backtest_rejects_premarket_bars() -> None:
    bars = _session_bars()
    premarket = datetime(2026, 11, 5, 23, 0, tzinfo=timezone(timedelta(hours=9)))
    us_bars = [MinuteBar(premarket + timedelta(minutes=i), bar.open, bar.high, bar.low,
                         bar.close, bar.volume, bar.spread_bps) for i, bar in enumerate(bars)]
    with pytest.raises(ValueError, match="official regular-session open"):
        backtest_orb_vwap("US", "AAPL", us_bars)
