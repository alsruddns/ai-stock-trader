from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "probe-kis-real-kr-websocket.py"
SPEC = importlib.util.spec_from_file_location("probe_kis_real_kr_websocket", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


SAFE_SWITCHES = {
    "TRADING_MODE": "paper",
    "LIVE_TRADING_ENABLED": "false",
    "KIS_ENVIRONMENT": "paper",
    "KIS_MARKET_DATA_ENABLED": "true",
    "KIS_WEBSOCKET_ENABLED": "true",
    "KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED": "true",
    "KIS_PAPER_ORDERS_ENABLED": "false",
    "KIS_REAL_ORDERS_ENABLED": "false",
    "PAPER_AUTOTRADING_ENABLED": "false",
    "KIS_REAL_ACCOUNT_READ_ENABLED": "false",
    "KIS_REAL_RECONCILIATION_ENABLED": "false",
    "KIS_REAL_EXECUTION_NOTICES_ENABLED": "false",
    "REAL_SIGNAL_MONITOR_ENABLED": "false",
    "REAL_SIGNAL_SUBMISSION_ENABLED": "false",
    "REAL_PROTECTIVE_EXIT_MANAGER_ENABLED": "false",
    "KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED": "false",
    "KIS_SHARED_RATE_LIMIT_ENABLED": "true",
}


class FakeWebSocket:
    def __init__(self, frames: list[str]):
        self.frames = frames
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        return self.frames.pop(0)

    async def pong(self, _payload: bytes) -> None:
        return None


class FakeConnection:
    def __init__(self, upstream: FakeWebSocket):
        self.upstream = upstream

    async def __aenter__(self) -> FakeWebSocket:
        return self.upstream

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.parametrize("unsafe", [
    ("KIS_REAL_ORDERS_ENABLED", "true"),
    ("KIS_PAPER_ORDERS_ENABLED", "true"),
    ("LIVE_TRADING_ENABLED", "true"),
    ("KIS_REAL_ACCOUNT_READ_ENABLED", "true"),
    ("KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED", "false"),
])
def test_probe_fails_closed_before_credentials_or_network(monkeypatch, capsys, unsafe) -> None:
    for key, value in SAFE_SWITCHES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(*unsafe)
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: Path(".env_local"))
    monkeypatch.setattr(probe.KisSettings, "from_environment", classmethod(
        lambda *_args: pytest.fail("unsafe settings must be rejected before credentials are loaded"),
    ))

    assert probe.main() == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "READ_ONLY_REAL_KR_STREAM_SWITCHES_NOT_SAFE"


@pytest.mark.parametrize(("frames", "expected_status"), [
    ([json.dumps({"header": {"tr_id": "H0STCNT0"}, "body": {"rt_cd": "0", "msg_cd": "OK"}})], "acknowledged_no_tick"),
    (["0|H0STCNT0|001|005930^09:30:00^73100^5"], "tick_received"),
])
def test_real_kr_probe_subscribes_and_unsubscribes_without_orders(frames, expected_status, monkeypatch) -> None:
    upstream = FakeWebSocket(frames)
    monkeypatch.setattr(probe, "approval_key", lambda _settings: "private-approval-token")
    monkeypatch.setattr(probe.websockets, "connect", lambda *_args, **_kwargs: FakeConnection(upstream))

    result = asyncio.run(probe.run_probe(
        SimpleNamespace(), SimpleNamespace(websocket_url="ws://real.invalid:21000"), "005930", 0,
    ))

    assert result["status"] == expected_status
    assert result["environment"] == "real_market_data_read_only"
    assert result["order_sent"] is False
    assert result["account_read_sent"] is False
    assert result["unsubscribe_sent"] is True
    assert upstream.sent[0]["header"]["approval_key"] == "private-approval-token"
    assert upstream.sent[0]["header"]["tr_type"] == "1"
    assert upstream.sent[0]["body"]["input"]["tr_id"] == "H0STCNT0"
    assert upstream.sent[0]["body"]["input"]["tr_key"] == "005930"
    assert upstream.sent[1]["header"]["tr_type"] == "0"


@pytest.mark.parametrize("symbol", ["AAPL", "12345", "005930x", ""])
def test_main_validates_domestic_symbol_before_approval(monkeypatch, capsys, symbol) -> None:
    for key, value in SAFE_SWITCHES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: Path(".env_local"))
    monkeypatch.setattr(probe.KisSettings, "from_environment", classmethod(
        lambda *_args: pytest.fail("invalid symbol must fail before credentials are loaded"),
    ))

    assert probe.main(symbol) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "INVALID_SUBSCRIPTION"


def test_probe_refuses_a_duplicate_socket_when_managed_real_feed_is_active(monkeypatch, capsys) -> None:
    for key, value in SAFE_SWITCHES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: Path(".env_local"))
    monkeypatch.setattr(probe, "local_real_feed_is_active", lambda: True)
    monkeypatch.setattr(probe.KisSettings, "from_environment", classmethod(
        lambda *_args: pytest.fail("an active managed REAL feed must block a second socket"),
    ))

    assert probe.main() == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "LOCAL_REAL_STREAM_ALREADY_ACTIVE"


def test_real_kr_probe_sanitizes_rejection(monkeypatch) -> None:
    upstream = FakeWebSocket([json.dumps({"header": {"tr_id": "H0STCNT0"}, "body": {
        "rt_cd": "1", "msg_cd": "SAFE_CODE", "msg1": "private broker response",
    }})])
    monkeypatch.setattr(probe, "approval_key", lambda _settings: "private-approval-token")
    monkeypatch.setattr(probe.websockets, "connect", lambda *_args, **_kwargs: FakeConnection(upstream))

    result = asyncio.run(probe.run_probe(
        SimpleNamespace(), SimpleNamespace(websocket_url="ws://real.invalid:21000"), "005930", 0,
    ))

    assert result["status"] == "rejected"
    assert result["broker_code"] == "SAFE_CODE"
    assert result["acknowledged"] is False
    assert result["unsubscribe_sent"] is False
    assert "private broker response" not in json.dumps(result)
    assert "private-approval-token" not in json.dumps(result)


def test_main_isolates_accounts_and_order_switches_before_real_stream(monkeypatch, capsys) -> None:
    for key, value in SAFE_SWITCHES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("KIS_REAL_APP_KEY", "private-real-app-key")
    monkeypatch.setenv("KIS_REAL_APP_SECRET", "private-real-app-secret")
    monkeypatch.setenv("KIS_REAL_ACCOUNT_NUMBER", "private-account-number")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "private-paper-app-key")
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: Path(".env_local"))
    monkeypatch.setattr(probe, "local_real_feed_is_active", lambda: False)

    settings = SimpleNamespace(enabled=True, app_key="private-real-app-key", app_secret="private-real-app-secret", shared_rate_limit_enabled=True)
    stream = SimpleNamespace(enabled=True, websocket_url="ws://real.invalid:21000")
    monkeypatch.setattr(probe.KisSettings, "from_environment", classmethod(lambda _cls, env: settings if env == "real" else None))
    monkeypatch.setattr(probe.StreamSettings, "from_environment", classmethod(lambda _cls, _env: stream))

    async def fake_probe(*args: object) -> dict[str, Any]:
        assert args[0] is settings
        assert probe.os.environ["TRADING_MODE"] == "paper"
        assert probe.os.environ["KIS_ENVIRONMENT"] == "paper"
        assert probe.os.environ["KIS_ACCOUNT_READ_ENABLED"] == "false"
        assert probe.os.environ["KIS_REAL_ACCOUNT_READ_ENABLED"] == "false"
        assert probe.os.environ["KIS_REAL_ORDERS_ENABLED"] == "false"
        assert probe.os.environ["KIS_PAPER_ORDERS_ENABLED"] == "false"
        assert probe.os.environ["KIS_REAL_APP_KEY"] == "private-real-app-key"
        assert "KIS_PAPER_APP_KEY" not in probe.os.environ
        assert "KIS_REAL_ACCOUNT_NUMBER" not in probe.os.environ
        return {"status": "acknowledged_no_tick", "order_sent": False, "account_read_sent": False}

    monkeypatch.setattr(probe, "run_probe", fake_probe)
    assert probe.main() == 0
    output = capsys.readouterr().out
    assert '"status": "acknowledged_no_tick"' in output
    assert '"order_sent": false' in output
    assert "private-real-app-key" not in output
    assert "private-real-app-secret" not in output
    assert "private-account-number" not in output
