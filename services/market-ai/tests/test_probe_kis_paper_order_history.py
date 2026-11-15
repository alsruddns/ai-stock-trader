from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


def _load_probe():
    repository = Path(__file__).resolve().parents[3]
    script = repository / "scripts" / "probe-kis-paper-order-history.py"
    spec = importlib.util.spec_from_file_location("kis_paper_order_history_probe", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _set_read_only_environment(monkeypatch) -> None:
    for key, value in {
        "TRADING_MODE": "paper", "LIVE_TRADING_ENABLED": "false",
        "PAPER_AUTOTRADING_ENABLED": "false", "MARKET_OPERATIONAL_SCOPE": "KR",
        "KIS_ENVIRONMENT": "paper", "KIS_ACCOUNT_READ_ENABLED": "true",
        "KIS_PAPER_ACCOUNT_READ_ENABLED": "true", "KIS_REAL_ACCOUNT_READ_ENABLED": "false",
        "KIS_PAPER_ORDERS_ENABLED": "false", "KIS_REAL_ORDERS_ENABLED": "false",
        "KIS_REAL_RECONCILIATION_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)


def test_paper_order_history_probe_sends_only_read_only_query_and_redacts_rows(monkeypatch, capsys) -> None:
    probe = _load_probe()
    _set_read_only_environment(monkeypatch)
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: Path(".env_local"))
    monkeypatch.setattr(probe.KisSettings, "from_environment", lambda _environment: SimpleNamespace(
        app_key="app-key", app_secret="app-secret", account_number="1234567801",
        shared_rate_limit_enabled=True, base_url="https://paper.invalid",
    ))

    class Response:
        status_code = 200
        headers = {"tr_cont": ""}

        @staticmethod
        def json():
            return {
                "rt_cd": "0", "msg_cd": "OK",
                "output1": [{"odno": "sensitive-order", "pdno": "005930", "rmn_qty": "3"}],
                "ctx_area_fk100": "", "ctx_area_nk100": "",
            }

    class Client:
        def __init__(self, _settings):
            self.closed = False

        @staticmethod
        def _access_token():
            return "access-token"

        @staticmethod
        def _get(url, *, headers, params):
            assert url == "https://paper.invalid" + probe.API_PATH
            assert headers["tr_id"] == "VTTC0081R"
            assert headers["tr_cont"] == ""
            assert params["CCLD_DVSN"] == "00"
            assert params["INQR_STRT_DT"] == params["INQR_END_DT"]
            return Response()

        def close(self):
            self.closed = True

    monkeypatch.setattr(probe, "KisReadOnlyClient", Client)
    requested_date = datetime.now(timezone(timedelta(hours=9))).date().isoformat()

    assert probe.main(requested_date) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "read_complete"
    assert result["returned_order_count"] == 1
    assert result["remaining_quantity_fields_observed"] is True
    assert result["positive_remaining_quantity_rows"] == 1
    assert result["cancel_preflight_verified"] is False
    assert result["order_or_cancel_sent"] is False
    assert "sensitive-order" not in json.dumps(result)
    assert "app-key" not in json.dumps(result)


def test_paper_order_history_probe_fails_closed_when_paper_orders_are_enabled(monkeypatch, capsys) -> None:
    probe = _load_probe()
    _set_read_only_environment(monkeypatch)
    monkeypatch.setenv("KIS_PAPER_ORDERS_ENABLED", "true")
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: Path(".env_local"))

    requested_date = datetime.now(timezone(timedelta(hours=9))).date().isoformat()

    assert probe.main(requested_date) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "blocked", "reason": "SAFETY_SWITCHES_NOT_READ_ONLY"}


def test_remaining_order_quantity_parser_rejects_boolean_and_malformed_values() -> None:
    probe = _load_probe()

    assert probe._valid_quantity("3") == 3
    assert probe._valid_quantity(0) == 0
    assert probe._valid_quantity(True) is None
    assert probe._valid_quantity("3.5") is None
    assert probe._valid_quantity("-1") is None
