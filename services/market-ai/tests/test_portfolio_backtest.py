from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.portfolio_backtest import backtest_domestic_portfolio
from app.quant import BacktestResult, MinuteBar, QuantConfig, Trade


def _bars() -> list[MinuteBar]:
    start = datetime(2026, 11, 5, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    return [MinuteBar(start + timedelta(minutes=i), Decimal("100"), Decimal("101"),
                      Decimal("99"), Decimal("100"), 1000, Decimal("5"),
                      start + timedelta(minutes=i, seconds=30)) for i in range(380)]


def _candidate(symbol: str, bars: list[MinuteBar], entry: int = 20, exit: int = 30,
               pnl: Decimal = Decimal("10")) -> BacktestResult:
    trade = Trade(bars[entry].timestamp, bars[exit].timestamp, Decimal("100"), Decimal("101"),
                  1, pnl, Decimal(0), pnl, "TEST", Decimal("1"))
    return BacktestResult("KR", symbol, bars[0].timestamp.date(), (trade,), pnl, Decimal(0), pnl,
                          len(bars), 1, False)


def _run(candidates, bars, **overrides):
    return backtest_domestic_portfolio(
        candidates, {candidate.symbol: bars for candidate in candidates},
        starting_equity=Decimal("1000000"), max_order_notional=Decimal("1000000"),
        config=QuantConfig(commission_bps=Decimal(0), slippage_bps=Decimal(0)), **overrides,
    )


def test_portfolio_sizes_positions_under_risk_allocation_and_shared_cash() -> None:
    bars = _bars()
    result = _run([_candidate("005930", bars)], bars)

    assert len(result.trades) == 1
    assert result.trades[0].quantity == 1000  # 10% allocation is tighter than the 0.25% stop-risk budget here.
    assert result.maximum_open_positions == 1
    assert result.executable is False


def test_portfolio_enforces_three_open_slots_without_reusing_same_minute_exits() -> None:
    bars = _bars()
    candidates = [_candidate(f"00000{i}", bars, entry=20, exit=30) for i in range(1, 5)]

    result = _run(candidates, bars)

    assert result.maximum_open_positions == 3
    assert len(result.trades) == 3
    assert result.rejected_entries == (("000004", "MAX_OPEN_POSITIONS"),)
    assert result.executable is False


def test_daily_loss_mark_to_market_blocks_later_entry_but_keeps_open_trade_management() -> None:
    bars = _bars()
    losing_bars = list(bars)
    stamp = losing_bars[25].timestamp
    losing_bars[25] = MinuteBar(stamp, Decimal("70"), Decimal("71"), Decimal("69"), Decimal("70"),
                                1000, Decimal("5"), stamp + timedelta(seconds=30))
    first = _candidate("000001", losing_bars, entry=20, exit=30)
    second = _candidate("000002", losing_bars, entry=26, exit=35)

    result = _run([first, second], losing_bars)

    assert result.daily_loss_limit_hit is True
    assert result.rejected_entries == (("000002", "DAILY_LOSS_LIMIT"),)
    assert len(result.trades) == 1
    assert result.executable is False


def test_three_consecutive_losses_block_subsequent_entries() -> None:
    bars = _bars()
    candidates = [
        _candidate("000001", bars, entry=20, exit=23, pnl=Decimal("-1")),
        _candidate("000002", bars, entry=20, exit=24, pnl=Decimal("-1")),
        _candidate("000003", bars, entry=20, exit=25, pnl=Decimal("-1")),
        _candidate("000004", bars, entry=26, exit=30),
    ]

    result = _run(candidates, bars)

    assert result.consecutive_loss_limit_hit is True
    assert result.rejected_entries == (("000004", "CONSECUTIVE_LOSS_LIMIT"),)


def test_rejects_us_and_mismatched_session_candidates() -> None:
    bars = _bars()
    candidate = _candidate("005930", bars)

    with pytest.raises(ValueError, match="only non-executable KR"):
        _run([replace(candidate, market="US")], bars)
    with pytest.raises(ValueError, match="session"):
        _run([replace(candidate, session=candidate.session + timedelta(days=1))], bars)
