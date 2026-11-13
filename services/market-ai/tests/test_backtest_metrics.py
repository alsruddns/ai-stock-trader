from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.backtest_metrics import (
    session_net_return_bps, summarize_sessions, summarize_spread_coverage, summarize_trades,
)
from app.quant import BacktestResult, MinuteBar, Trade


KR_TZ = ZoneInfo("Asia/Seoul")


def _trade(*, day: int, entry: str, quantity: int, gross: str, costs: str, net: str) -> Trade:
    timestamp = datetime(2026, 11, day, 10, 0, tzinfo=KR_TZ)
    return Trade(timestamp, timestamp, Decimal(entry), Decimal(entry), quantity,
                 Decimal(gross), Decimal(costs), Decimal(net), "SESSION_END")


def test_cost_adjusted_returns_use_entry_notional_and_do_not_compound() -> None:
    trades = [
        _trade(day=2, entry="100", quantity=10, gross="50", costs="10", net="40"),
        _trade(day=3, entry="200", quantity=5, gross="-20", costs="10", net="-30"),
    ]

    summary = summarize_trades(trades)

    assert summary["trade_count"] == 2
    assert summary["win_rate_percent"] == "50"
    assert Decimal(summary["profit_factor_net"]) == Decimal(4) / Decimal(3)
    assert Decimal(summary["mean_gross_return_bps"]) == Decimal("150")
    assert Decimal(summary["mean_cost_bps"]) == Decimal("100")
    assert Decimal(summary["mean_net_return_bps"]) == Decimal("50")
    assert Decimal(summary["median_net_return_bps"]) == Decimal("50")
    assert Decimal(summary["worst_trade_net_return_bps"]) == Decimal("-300")
    assert "not compounded" in summary["return_basis"]


def test_no_trades_and_no_losing_trades_do_not_report_fake_profit_factor() -> None:
    empty = summarize_trades([])
    no_losses = summarize_trades([
        _trade(day=2, entry="100", quantity=1, gross="10", costs="2", net="8"),
    ])
    break_even = summarize_trades([
        _trade(day=3, entry="100", quantity=1, gross="0", costs="0", net="0"),
    ])

    assert empty["mean_net_return_bps"] is None
    assert empty["profit_factor_status"] == "no_trades"
    assert no_losses["profit_factor_net"] is None
    assert no_losses["profit_factor_status"] == "no_losing_trades"
    assert break_even["profit_factor_status"] == "break_even_only"


def test_invalid_entry_notional_is_rejected() -> None:
    invalid = _trade(day=2, entry="0", quantity=1, gross="0", costs="0", net="0")

    with pytest.raises(ValueError, match="positive entry and exit prices"):
        summarize_trades([invalid])


def test_trade_summary_rejects_invalid_quantity_and_inconsistent_pnl() -> None:
    boolean_quantity = _trade(day=2, entry="100", quantity=True, gross="10", costs="2", net="8")
    inconsistent_pnl = _trade(day=3, entry="100", quantity=1, gross="10", costs="2", net="9")

    with pytest.raises(ValueError, match="positive integer quantity"):
        summarize_trades([boolean_quantity])
    with pytest.raises(ValueError, match="gross PnL less costs"):
        summarize_trades([inconsistent_pnl])


def test_trade_summary_rejects_invalid_prices_and_costs() -> None:
    invalid_exit = replace(
        _trade(day=2, entry="100", quantity=1, gross="10", costs="2", net="8"),
        exit_price=Decimal("0"),
    )
    negative_cost = _trade(day=3, entry="100", quantity=1, gross="10", costs="-2", net="12")

    with pytest.raises(ValueError, match="positive entry and exit prices"):
        summarize_trades([invalid_exit])
    with pytest.raises(ValueError, match="costs must be non-negative"):
        summarize_trades([negative_cost])


def test_spread_coverage_separates_numeric_values_from_timestamped_strategy_inputs() -> None:
    start = datetime(2026, 11, 10, 9, 0, tzinfo=KR_TZ)
    bars = [
        MinuteBar(start, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"),
                  100, Decimal("5"), start + timedelta(seconds=10)),
        MinuteBar(start + timedelta(minutes=1), Decimal("100"), Decimal("101"), Decimal("99"),
                  Decimal("100"), 100, Decimal("5")),
        MinuteBar(start + timedelta(minutes=2), Decimal("100"), Decimal("101"), Decimal("99"),
                  Decimal("100"), 100),
    ]

    coverage = summarize_spread_coverage(bars)

    assert coverage == {
        "spread_bars": 2,
        "spread_coverage_percent": 66.67,
        "spread_observation_time_bars": 1,
        "spread_observation_time_coverage_percent": 33.33,
        "spread_filter_has_complete_input": False,
    }


def test_session_performance_keeps_no_trade_sessions_and_bad_days_visible() -> None:
    winning_trade = _trade(day=2, entry="100", quantity=1,
        gross="20", costs="5", net="15")
    losing_trade = _trade(day=3, entry="100", quantity=1,
        gross="-10", costs="5", net="-15")
    sessions = [
        BacktestResult("KR", "005930", winning_trade.entry_time.date(), (winning_trade,),
            Decimal("20"), Decimal("5"), Decimal("15"), 380, 1),
        BacktestResult("KR", "005930", losing_trade.entry_time.date(), (losing_trade,),
            Decimal("-10"), Decimal("5"), Decimal("-15"), 380, 1),
        BacktestResult("KR", "005930", _trade(day=4, entry="100", quantity=1,
            gross="0", costs="0", net="0").entry_time.date(), (),
            Decimal("0"), Decimal("0"), Decimal("0"), 380, 0),
    ]

    summary = summarize_sessions(sessions)

    assert summary["session_count"] == 3
    assert summary["sessions_with_trades"] == 2
    assert summary["sessions_without_trades"] == 1
    assert summary["positive_net_pnl_sessions"] == 1
    assert summary["negative_net_pnl_sessions"] == 1
    assert summary["zero_net_pnl_sessions"] == 1
    assert Decimal(summary["mean_net_pnl_per_session"]) == 0
    assert Decimal(summary["median_net_pnl_per_session"]) == 0
    assert Decimal(summary["worst_session_net_pnl"]) == -15
    assert Decimal(summary["best_session_net_pnl"]) == 15
    assert summary["sessions_with_return"] == 2
    assert Decimal(summary["mean_net_return_bps_on_traded_notional"]) == 0
    assert Decimal(summary["median_net_return_bps_on_traded_notional"]) == 0
    assert Decimal(summary["worst_session_net_return_bps_on_traded_notional"]) < 0
    assert Decimal(summary["best_session_net_return_bps_on_traded_notional"]) > 0
    assert session_net_return_bps(sessions[0]) == Decimal("1500")
    assert session_net_return_bps(sessions[2]) is None


def test_session_performance_rejects_duplicate_dates_and_inconsistent_net_pnl() -> None:
    timestamp = _trade(day=2, entry="100", quantity=1,
        gross="10", costs="2", net="8").entry_time
    valid = BacktestResult("KR", "005930", timestamp.date(), (),
        Decimal("0"), Decimal("0"), Decimal("0"), 380, 0)
    inconsistent = BacktestResult("KR", "005930", timestamp.date(), (),
        Decimal("10"), Decimal("2"), Decimal("9"), 380, 0)

    with pytest.raises(ValueError, match="unique market, symbol, and date"):
        summarize_sessions([valid, valid])
    with pytest.raises(ValueError, match="gross PnL less costs"):
        summarize_sessions([inconsistent])


def test_session_performance_rejects_negative_costs_and_pnl_without_trades() -> None:
    timestamp = _trade(day=2, entry="100", quantity=1,
        gross="10", costs="-2", net="12").entry_time
    negative_cost = BacktestResult("KR", "005930", timestamp.date(), (
        _trade(day=2, entry="100", quantity=1, gross="10", costs="-2", net="12"),
        _trade(day=2, entry="100", quantity=1, gross="10", costs="4", net="6"),
    ), Decimal("20"), Decimal("2"), Decimal("18"), 380, 2)
    no_trade_pnl = BacktestResult("KR", "005930", timestamp.date(), (),
        Decimal("10"), Decimal("0"), Decimal("10"), 380, 0)

    with pytest.raises(ValueError, match="trade costs must be non-negative"):
        summarize_sessions([negative_cost])
    with pytest.raises(ValueError, match="without trades must have zero PnL"):
        summarize_sessions([no_trade_pnl])


def test_session_performance_allows_only_sub_atto_decimal_sum_rounding() -> None:
    from app.backtest_metrics import _amounts_match

    assert _amounts_match(Decimal("10"), Decimal("10.0000000000000000009"))
    assert not _amounts_match(Decimal("10"), Decimal("10.000000000001"))
