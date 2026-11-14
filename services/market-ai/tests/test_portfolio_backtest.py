from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.portfolio_backtest import (
    aggregate_compounded_drawdown, analyze_capital_sensitivity, analyze_slippage_sensitivity,
    backtest_domestic_portfolio,
)
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


def _shift_bars(bars: list[MinuteBar], days: int) -> list[MinuteBar]:
    return [MinuteBar(
        bar.timestamp + timedelta(days=days), bar.open, bar.high, bar.low, bar.close,
        bar.volume, bar.spread_bps,
        bar.spread_observed_at + timedelta(days=days) if bar.spread_observed_at else None,
    ) for bar in bars]


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


def test_portfolio_position_sizing_reserves_the_configured_maximum_exit_spread(monkeypatch) -> None:
    _signals_at_lengths(monkeypatch, {0: {21}})
    monkeypatch.setattr("app.portfolio_backtest._atr", lambda _bars, _period: Decimal("10"))
    data = {"005930": _bars(0), "000660": _bars(1)}
    config = QuantConfig(
        commission_bps=Decimal(0), slippage_bps=Decimal(0), maximum_spread_bps=Decimal("1000"),
    )

    result = backtest_domestic_portfolio(
        data, starting_equity=Decimal("100000"), max_order_notional=Decimal("100000"), config=config,
    )

    assert len(result.trades) == 1
    # Entry spread is only 5 bps; risk sizing must reserve up to the 1,000 bps
    # allowed at a later stop exit, rather than sizing from the entry quote alone.
    assert result.trades[0].quantity == 12
    entry_fill = Decimal("102") * (Decimal(1) + Decimal("5") / Decimal(2) / Decimal(10_000))
    stop_price = entry_fill - Decimal("15")
    worst_case_stop_risk_per_share = Decimal("15") + stop_price * Decimal("1000") / Decimal(2) / Decimal(10_000)
    assert result.trades[0].quantity * worst_case_stop_risk_per_share <= Decimal("250")
    assert result.executable is False


def test_portfolio_reports_trade_risk_as_the_one_share_entry_blocker(monkeypatch) -> None:
    _signals_at_lengths(monkeypatch, {0: {21}})
    monkeypatch.setattr("app.portfolio_backtest._atr", lambda _bars, _period: Decimal("100"))
    data = {"005930": _bars(0), "000660": _bars(1)}

    result = backtest_domestic_portfolio(
        data, starting_equity=Decimal("10000"), max_order_notional=Decimal("1000000"),
        config=QuantConfig(commission_bps=Decimal(0), slippage_bps=Decimal(0)),
    )

    assert result.quant_signals_by_symbol == {"000660": 0, "005930": 1}
    assert result.rejected_entries == (("005930", "TRADE_RISK_LIMIT_BELOW_ONE_SHARE"),)
    assert result.trades == ()
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


def test_portfolio_gross_cost_and_net_totals_reconcile() -> None:
    data = {"005930": _strategy_bars(0), "000660": _strategy_bars(1)}
    config = QuantConfig(
        commission_bps=Decimal(0), buy_commission_bps=Decimal("14.7"),
        sell_commission_bps=Decimal("14.7"), sell_tax_bps=Decimal("20"),
        slippage_bps=Decimal("10"), minimum_turnover=Decimal(0),
        minimum_relative_volume=Decimal("1.5"),
    )

    result = backtest_domestic_portfolio(
        data, starting_equity=Decimal("1000000"), max_order_notional=Decimal("1000000"), config=config,
    )

    assert len(result.trades) == 2
    assert result.gross_pnl == sum((trade.gross_pnl for trade in result.trades), Decimal(0))
    assert result.estimated_costs == sum((trade.costs for trade in result.trades), Decimal(0))
    assert result.net_pnl == sum((trade.net_pnl for trade in result.trades), Decimal(0))
    assert all(sum(trade.costs_by_component.values(), Decimal(0)) == trade.costs for trade in result.trades)
    assert sum(result.estimated_costs_by_component.values(), Decimal(0)) == result.estimated_costs
    assert result.estimated_costs_by_component["buy_commission_and_exchange_fee"] > 0
    assert result.estimated_costs_by_component["sell_commission_and_exchange_fee"] > 0
    assert result.estimated_costs_by_component["sell_tax"] > 0
    assert result.estimated_costs_by_component["entry_slippage"] > 0
    assert result.estimated_costs_by_component["exit_slippage"] > 0
    assert result.estimated_costs_by_component["entry_spread"] > 0
    assert result.estimated_costs_by_component["exit_spread"] > 0
    assert abs(result.gross_pnl - result.estimated_costs - result.net_pnl) <= Decimal("1e-18")
    assert result.ending_equity == result.starting_equity + result.net_pnl


def test_slippage_sensitivity_replays_same_sessions_and_reports_non_executable_results() -> None:
    first = {"005930": _strategy_bars(0), "000660": _strategy_bars(1)}
    second = {symbol: _shift_bars(bars, 1) for symbol, bars in first.items()}
    config = QuantConfig(
        commission_bps=Decimal(0), buy_commission_bps=Decimal("14.7"),
        sell_commission_bps=Decimal("14.7"), sell_tax_bps=Decimal("20"),
        slippage_bps=Decimal("10"), minimum_turnover=Decimal(0),
        minimum_relative_volume=Decimal("1.5"),
    )

    results = analyze_slippage_sensitivity(
        {date(2026, 11, 5): first, date(2026, 11, 6): second},
        starting_equity=Decimal("1000000"), max_order_notional=Decimal("1000000"),
        config=config, slippage_scenarios_bps=[Decimal(0), Decimal(10), Decimal(20)],
    )

    assert [item.slippage_bps_per_fill for item in results] == [Decimal(0), Decimal(10), Decimal(20)]
    assert all(item.session_count == 2 and item.accepted_trade_count == 4 for item in results)
    assert results[0].estimated_costs < results[1].estimated_costs < results[2].estimated_costs
    assert results[0].net_pnl > results[1].net_pnl > results[2].net_pnl
    assert all(item.ending_equity is None for item in results)
    assert all(item.drawdown_basis == "maximum_independent_session" for item in results)
    assert all(item.executable is False for item in results)

    carried = analyze_slippage_sensitivity(
        {date(2026, 11, 5): first, date(2026, 11, 6): second},
        starting_equity=Decimal("1000000"), max_order_notional=Decimal("1000000"),
        config=config, slippage_scenarios_bps=[Decimal(0), Decimal(10)],
        carry_equity_between_sessions=True,
    )
    assert all(abs(item.ending_equity - (Decimal("1000000") + item.net_pnl)) <= Decimal("1e-8")
               for item in carried)
    assert all(item.drawdown_basis == "compounded_sequential_sessions" for item in carried)
    assert all(item.executable is False for item in carried)

    with pytest.raises(ValueError, match="unique"):
        analyze_slippage_sensitivity(
            {date(2026, 11, 5): first}, starting_equity=Decimal("1000000"),
            max_order_notional=Decimal("1000000"), config=config,
            slippage_scenarios_bps=[Decimal(5), Decimal(5)],
        )


def test_capital_sensitivity_scales_percentage_caps_and_reports_rejections() -> None:
    first = {"005930": _strategy_bars(0), "000660": _strategy_bars(1)}
    second = {symbol: _shift_bars(bars, 1) for symbol, bars in first.items()}
    config = QuantConfig(
        commission_bps=Decimal(0), slippage_bps=Decimal(0),
        minimum_turnover=Decimal(0), minimum_relative_volume=Decimal("1.5"),
    )

    results = analyze_capital_sensitivity(
        {date(2026, 11, 5): first, date(2026, 11, 6): second},
        starting_equity=Decimal("1000000"), max_order_notional=Decimal("1000000"),
        config=config, capital_scenarios=[Decimal("1000000"), Decimal("2000000")],
    )

    assert [item.starting_equity for item in results] == [Decimal("1000000"), Decimal("2000000")]
    assert [item.max_order_notional for item in results] == [Decimal("1000000"), Decimal("2000000")]
    assert all(item.session_count == 2 for item in results)
    assert all(item.accepted_trade_count > 0 for item in results)
    assert all(abs(item.gross_pnl - item.estimated_costs - item.net_pnl) <= Decimal("1e-18")
               for item in results)
    assert all(item.ending_equity is None for item in results)
    assert all(item.drawdown_basis == "maximum_independent_session" for item in results)
    assert all(item.executable is False for item in results)
    for item in results:
        assert sum(item.estimated_costs_by_component.values(), Decimal(0)) == item.estimated_costs
        assert set(item.trade_outcomes_by_symbol) == {"000660", "005930"}
        assert sum(outcome["trade_count"] for outcome in item.trade_outcomes_by_symbol.values()) == item.accepted_trade_count
        assert sum((outcome["gross_pnl"] for outcome in item.trade_outcomes_by_symbol.values()), Decimal(0)) == item.gross_pnl
        assert sum((outcome["estimated_costs"] for outcome in item.trade_outcomes_by_symbol.values()), Decimal(0)) == item.estimated_costs
        assert all(
            abs(sum((outcome["estimated_costs_by_component"][name]
                     for outcome in item.trade_outcomes_by_symbol.values()), Decimal(0)) - amount)
            <= Decimal("1e-18")
            for name, amount in item.estimated_costs_by_component.items()
        )
        assert sum((outcome["net_pnl"] for outcome in item.trade_outcomes_by_symbol.values()), Decimal(0)) == item.net_pnl
        assert all(
            outcome["winning_trade_count"] + outcome["losing_trade_count"] <= outcome["trade_count"]
            for outcome in item.trade_outcomes_by_symbol.values()
        )
        assert all(
            abs(outcome["gross_pnl"] - outcome["estimated_costs"] - outcome["net_pnl"]) <= Decimal("1e-18")
            for outcome in item.trade_outcomes_by_symbol.values()
        )
        assert all(
            abs(sum(outcome["estimated_costs_by_component"].values(), Decimal(0)) - outcome["estimated_costs"])
            <= Decimal("1e-18")
            for outcome in item.trade_outcomes_by_symbol.values()
        )
        grouped_counts: dict[str, int] = {}
        for reasons in item.rejected_entries_by_symbol_and_reason.values():
            for reason, count in reasons.items():
                grouped_counts[reason] = grouped_counts.get(reason, 0) + count
        assert grouped_counts == item.rejected_entries_by_reason

    with pytest.raises(ValueError, match="unique"):
        analyze_capital_sensitivity(
            {date(2026, 11, 5): first}, starting_equity=Decimal("1000000"),
            max_order_notional=Decimal("1000000"), config=config,
            capital_scenarios=[Decimal("1000000"), Decimal("1000000")],
        )


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
    # The fourth ticker has the strongest signal-bar liquidity despite sorting last.
    narrow = data["000001"][20]
    data["000001"][20] = MinuteBar(
        narrow.timestamp, narrow.open, narrow.high, narrow.low, narrow.close,
        1001, Decimal("1"), narrow.spread_observed_at,
    )
    strongest = data["000004"][20]
    data["000004"][20] = MinuteBar(
        strongest.timestamp, strongest.open, strongest.high, strongest.low, strongest.close,
        5000, strongest.spread_bps, strongest.spread_observed_at,
    )

    result = _run(data)

    assert result.maximum_open_positions == 3
    assert len(result.trades) == 3
    assert {trade.symbol for trade in result.trades} == {"000004", "000003", "000001"}
    assert result.signals_by_symbol == {"000001": 1, "000002": 1, "000003": 1, "000004": 1}
    assert result.signals_by_symbol["000004"] == 1
    assert result.rejected_entries == (("000002", "MAX_OPEN_POSITIONS"),)
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
    assert result.max_bar_marked_drawdown_amount > 0
    assert result.max_bar_marked_drawdown_percent > 0
    assert result.bar_marked_equity_observations == len(first_bars) * 3
    assert result.executable is False


def test_flat_portfolio_reports_zero_bar_marked_drawdown() -> None:
    bars = _bars()

    result = _run({"005930": bars, "000660": _bars(1)})

    assert result.max_bar_marked_drawdown_amount == 0
    assert result.max_bar_marked_drawdown_percent == 0
    assert result.bar_marked_equity_observations == len(bars) * 3


def test_compounded_drawdown_carries_prior_session_high_water_mark() -> None:
    baseline = _run({"005930": _bars(0), "000660": _bars(1)})
    first = replace(
        baseline, max_bar_marked_drawdown_amount=Decimal("200000"),
        max_bar_marked_drawdown_percent=Decimal("18.1818181818"),
        minimum_bar_marked_equity=Decimal("900000"),
        maximum_bar_marked_equity=Decimal("1100000"),
    )
    second = replace(
        baseline, max_bar_marked_drawdown_amount=Decimal("150000"),
        max_bar_marked_drawdown_percent=Decimal("14.2857142857"),
        minimum_bar_marked_equity=Decimal("800000"),
        maximum_bar_marked_equity=Decimal("1050000"),
        session=baseline.session + timedelta(days=1),
    )

    result = aggregate_compounded_drawdown(Decimal("1000000"), [first, second])

    assert result.maximum_amount == Decimal("300000")
    assert result.maximum_percent == Decimal("27.27272727272727272727272727")
    assert result.observations == baseline.bar_marked_equity_observations * 2


def test_compounded_drawdown_rejects_nonchronological_or_disconnected_sessions() -> None:
    baseline = _run({"005930": _bars(0), "000660": _bars(1)})
    next_session = replace(baseline, session=baseline.session + timedelta(days=1))
    disconnected_start = Decimal("999999")
    disconnected_session = replace(
        next_session, starting_equity=disconnected_start,
        ending_equity=disconnected_start + next_session.net_pnl,
    )

    with pytest.raises(ValueError, match="strictly chronological"):
        aggregate_compounded_drawdown(Decimal("1000000"), [next_session, baseline])
    with pytest.raises(ValueError, match="not sequential"):
        aggregate_compounded_drawdown(Decimal("1000000"), [baseline, disconnected_session])


def test_compounded_drawdown_rejects_duplicate_sessions_and_invalid_observation_counts() -> None:
    baseline = _run({"005930": _bars(0), "000660": _bars(1)})
    duplicate = replace(baseline)
    invalid_observations = replace(
        baseline, session=baseline.session + timedelta(days=1), bar_marked_equity_observations=True,
    )

    with pytest.raises(ValueError, match="strictly chronological"):
        aggregate_compounded_drawdown(Decimal("1000000"), [baseline, duplicate])
    with pytest.raises(ValueError, match="equity marks"):
        aggregate_compounded_drawdown(Decimal("1000000"), [baseline, invalid_observations])


def test_compounded_drawdown_rejects_session_equity_that_does_not_reconcile_with_pnl() -> None:
    baseline = _run({"005930": _bars(0), "000660": _bars(1)})
    inconsistent = replace(baseline, ending_equity=baseline.ending_equity + Decimal("1"))

    with pytest.raises(ValueError, match="PnL does not reconcile"):
        aggregate_compounded_drawdown(Decimal("1000000"), [inconsistent])


def test_portfolio_drawdown_marks_adverse_intrabar_low_before_exit(monkeypatch) -> None:
    _signals_at_lengths(monkeypatch, {0: {21}})
    monkeypatch.setattr("app.portfolio_backtest._atr", lambda _bars, _period: Decimal("10"))
    first = _strategy_bars(0)
    stamp = first[22].timestamp
    first[22] = MinuteBar(stamp, Decimal("102.5"), Decimal("103"), Decimal("95"), Decimal("100"),
                          1800, Decimal("5"), stamp + timedelta(seconds=30))
    config = QuantConfig(
        commission_bps=Decimal(0), slippage_bps=Decimal(0), atr_stop_multiple=Decimal("1"),
        reward_risk_multiple=Decimal("2"), minimum_turnover=Decimal(0),
        minimum_relative_volume=Decimal("1.5"),
    )

    result = _run({"005930": first, "000660": _strategy_bars(1)}, config=config)

    assert len(result.trades) == 1
    assert result.bar_marked_equity_observations == len(first) * 3
    assert result.max_bar_marked_drawdown_amount > abs(result.net_pnl)
    assert "adverse low" in result.bar_marked_equity_basis


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
    assert set(result.rejected_entries) == {
        (symbol, "CONSECUTIVE_LOSS_LIMIT") for symbol in data
    }
    assert result.daily_loss_limit_hit is False
    assert result.ending_consecutive_losses == 3


def test_compounded_session_continues_consecutive_loss_streak(monkeypatch) -> None:
    _signals_at_lengths(monkeypatch, {index: {21} for index in range(2)})
    monkeypatch.setattr("app.portfolio_backtest._atr", lambda _bars, _period: Decimal("10"))
    first_session = {f"00000{index + 1}": _bars(index) for index in range(2)}
    second_session = {f"00000{index + 1}": _bars(index, session_date=6) for index in range(2)}
    for dataset in (first_session, second_session):
        for bars in dataset.values():
            stamp = bars[22].timestamp
            bars[22] = MinuteBar(stamp, Decimal("100"), Decimal("101"), Decimal("95"), Decimal("96"),
                                 1000, Decimal("5"), stamp + timedelta(seconds=30))

    first = _run(first_session)
    _signals_at_lengths(monkeypatch, {0: {21}, 1: {24}})
    second = backtest_domestic_portfolio(
        second_session, starting_equity=first.ending_equity, max_order_notional=Decimal("1000000"),
        config=QuantConfig(commission_bps=Decimal(0), slippage_bps=Decimal(0)),
        initial_consecutive_losses=first.ending_consecutive_losses,
    )

    assert len(first.trades) == 2
    assert first.ending_consecutive_losses == 2
    assert len(second.trades) == 1
    assert second.consecutive_loss_limit_hit is True
    assert second.ending_consecutive_losses == 3
    assert second.rejected_entries == (("000002", "CONSECUTIVE_LOSS_LIMIT"),)


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
