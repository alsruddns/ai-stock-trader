from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "probe-kis-real-us-quote.py"
SPEC = importlib.util.spec_from_file_location("probe_kis_real_us_quote", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def valid_environment() -> dict[str, str]:
    return {
        "TRADING_MODE": "paper",
        "LIVE_TRADING_ENABLED": "false",
        "KIS_PAPER_ORDERS_ENABLED": "false",
        "KIS_REAL_ORDERS_ENABLED": "false",
        "KIS_REAL_RECONCILIATION_ENABLED": "false",
        "KIS_REAL_ACCOUNT_READ_ENABLED": "false",
        "PAPER_AUTOTRADING_ENABLED": "false",
        "KIS_MARKET_DATA_ENABLED": "true",
        "KIS_REAL_MARKET_DATA_ENABLED": "true",
        "KIS_SHARED_RATE_LIMIT_ENABLED": "true",
        "KIS_REAL_APP_KEY": "real-key",
        "KIS_REAL_APP_SECRET": "real-secret",
        "POSTGRES_PASSWORD": "local-only",
    }


def test_real_quote_probe_requires_only_read_only_real_market_gates() -> None:
    PROBE.validate_configuration(valid_environment())


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TRADING_MODE", "real"),
        ("LIVE_TRADING_ENABLED", "true"),
        ("KIS_PAPER_ORDERS_ENABLED", "true"),
        ("KIS_REAL_ORDERS_ENABLED", "true"),
        ("KIS_REAL_RECONCILIATION_ENABLED", "true"),
        ("KIS_REAL_ACCOUNT_READ_ENABLED", "true"),
        ("PAPER_AUTOTRADING_ENABLED", "true"),
        ("KIS_MARKET_DATA_ENABLED", "false"),
        ("KIS_REAL_MARKET_DATA_ENABLED", "false"),
        ("KIS_SHARED_RATE_LIMIT_ENABLED", "false"),
        ("KIS_REAL_APP_SECRET", ""),
        ("POSTGRES_PASSWORD", ""),
    ],
)
def test_real_quote_probe_fails_closed_for_unsafe_or_incomplete_configuration(name: str, value: str) -> None:
    environment = valid_environment()
    environment[name] = value
    with pytest.raises(ValueError):
        PROBE.validate_configuration(environment)


def test_real_quote_probe_success_result_never_contains_quote_price_or_credentials() -> None:
    result = PROBE.safe_success_result("AAPL", "NAS", "USD", "2026-11-08T00:00:00+00:00", True)
    assert result["status"] == "ok"
    assert result["environment"] == "real_market_data_read_only"
    assert result["orders_called"] is False
    assert result["account_reads_called"] is False
    assert "price" not in result
    assert "real-secret" not in str(result)
