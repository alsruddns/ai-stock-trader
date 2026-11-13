from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "probe-kis-paper-cancel-preflight.py"
SPEC = importlib.util.spec_from_file_location("probe_kis_paper_cancel_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


SAFE_SWITCHES = {
    "TRADING_MODE": "paper",
    "LIVE_TRADING_ENABLED": "false",
    "KIS_ENVIRONMENT": "paper",
    "KIS_ACCOUNT_READ_ENABLED": "true",
    "KIS_PAPER_ACCOUNT_READ_ENABLED": "true",
    "KIS_REAL_ACCOUNT_READ_ENABLED": "false",
    "KIS_PAPER_ORDERS_ENABLED": "false",
    "KIS_REAL_ORDERS_ENABLED": "false",
    "KIS_REAL_RECONCILIATION_ENABLED": "false",
}


class _Response:
    status_code = 200

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, _settings: object, payload: dict[str, Any]):
        self.payload = payload
        self.closed = False
        self.expected_transaction_id = "VTTC0084R"
        self.expected_query_kind = "current_item_sequence"

    def _access_token(self) -> str:
        return "redacted-test-token"

    def _get(self, url: str, *, headers: dict[str, str], params: dict[str, str]) -> _Response:
        assert url.endswith("/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl")
        assert headers["tr_id"] == self.expected_transaction_id
        assert headers["custtype"] == "P"
        assert params["INQR_DVSN_1"] == ("0" if self.expected_query_kind == "legacy_order_sequence" else "1")
        assert params["INQR_DVSN_2"] == "0"
        return _Response(self.payload)

    def close(self) -> None:
        self.closed = True


def _configure_safe_probe(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any], transaction_id: str = "TTTC0084R",
) -> _FakeClient:
    for key, value in SAFE_SWITCHES.items():
        monkeypatch.setenv(key, value)
    settings = SimpleNamespace(
        app_key="test-app-key", app_secret="test-app-secret", account_number="1234567801",
        base_url="https://paper.invalid", shared_rate_limit_enabled=True,
    )
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: Path(".env_local"))
    monkeypatch.setattr(probe.KisSettings, "from_environment", classmethod(lambda _cls, _environment: settings))
    client = _FakeClient(settings, payload)
    client.expected_transaction_id = transaction_id
    monkeypatch.setattr(probe, "KisReadOnlyClient", lambda _settings: client)
    return client


def test_paper_preflight_probe_uses_official_transaction_id_and_reports_only_sanitized_shape(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    client = _configure_safe_probe(monkeypatch, {
        "rt_cd": "0", "msg_cd": "OK", "output": [{"psbl_qty": "2", "odno": "private-order-number"}],
    })

    assert probe.main() == 0

    output = capsys.readouterr().out
    assert '"status": "valid_preflight_response"' in output
    assert '"broker_result_code": "0"' in output
    assert '"transaction_id": "TTTC0084R"' in output
    assert '"returned_row_count": 1' in output
    assert '"cancel_quantity_field_observed": true' in output
    assert '"order_or_cancel_sent": false' in output
    assert "private-order-number" not in output
    assert "test-app-secret" not in output
    assert client.closed


def test_legacy_official_transaction_maps_to_paper_id_without_unlocking_cancel(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    client = _configure_safe_probe(monkeypatch, {
        "rt_cd": "0", "msg_cd": "OK", "output": [{"psbl_qty": "2", "odno": "private-order-number"}],
    }, transaction_id="VTTC8036R")
    client.expected_query_kind = "legacy_order_sequence"

    assert probe.main("TTTC8036R") == 0

    output = capsys.readouterr().out
    assert '"transaction_id": "TTTC8036R"' in output
    assert '"request_transaction_id": "VTTC8036R"' in output
    assert '"query_kind": "legacy_order_sequence"' in output
    assert '"cancel_quantity_field_observed": true' in output
    assert '"order_or_cancel_sent": false' in output
    assert "private-order-number" not in output
    assert client.closed


@pytest.mark.parametrize("transaction_id", ["VTTC0084R", "VTTC8036R", "TTTC0000R"])
def test_probe_rejects_unverified_transaction_ids_before_loading_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys, transaction_id: str,
) -> None:
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: pytest.fail("credentials must not be loaded"))
    monkeypatch.setattr(probe, "KisReadOnlyClient", lambda _settings: pytest.fail("KIS client must not be created"))

    assert probe.main(transaction_id) == 2

    assert '"reason": "UNSUPPORTED_TRANSACTION_ID"' in capsys.readouterr().out


@pytest.mark.parametrize(("key", "unsafe_value"), [
    ("KIS_PAPER_ORDERS_ENABLED", "true"),
    ("KIS_REAL_ACCOUNT_READ_ENABLED", "true"),
    ("LIVE_TRADING_ENABLED", "true"),
])
def test_probe_fails_closed_before_client_creation_when_switches_are_unsafe(
    monkeypatch: pytest.MonkeyPatch, capsys, key: str, unsafe_value: str,
) -> None:
    for safe_key, safe_value in SAFE_SWITCHES.items():
        monkeypatch.setenv(safe_key, safe_value)
    monkeypatch.setenv(key, unsafe_value)
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: Path(".env_local"))
    monkeypatch.setattr(probe, "KisReadOnlyClient", lambda _settings: pytest.fail("KIS client must not be created"))

    assert probe.main() == 2
    assert '"reason": "SAFETY_SWITCHES_NOT_READ_ONLY"' in capsys.readouterr().out


def test_unsupported_paper_service_code_is_not_treated_as_cancelable(
    monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    client = _configure_safe_probe(monkeypatch, {
        "rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "private broker response",
    })

    assert probe.main() == 3

    output = capsys.readouterr().out
    assert '"status": "unsupported_or_rejected"' in output
    assert '"broker_code": "OPSQ0002"' in output
    assert '"broker_result_code": "1"' in output
    assert "private broker response" not in output
    assert client.closed


@pytest.mark.parametrize("rows", [
    [],
    [{"odno": "private-order-number"}],
    [{"psbl_qty": None}],
    [{"PSBL_QTY": -1}],
    [{"psbl_qty": True}],
    [{"psbl_qty": "unknown"}],
])
def test_successful_preflight_without_a_valid_cancel_quantity_is_incomplete(
    monkeypatch: pytest.MonkeyPatch, capsys, rows: list[dict[str, Any]],
) -> None:
    client = _configure_safe_probe(monkeypatch, {"rt_cd": "0", "msg_cd": "OK", "output": rows})

    assert probe.main() == 3

    output = capsys.readouterr().out
    assert '"status": "response_incomplete"' in output
    assert '"cancel_quantity_field_observed": false' in output
    assert '"order_or_cancel_sent": false' in output
    assert "private-order-number" not in output
    assert client.closed
