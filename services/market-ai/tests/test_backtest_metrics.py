from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.backtest_metrics import summarize_trades
from app.quant import Trade


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

    with pytest.raises(ValueError, match="positive quantity and entry price"):
        summarize_trades([invalid])
