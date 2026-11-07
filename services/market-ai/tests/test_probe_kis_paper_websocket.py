from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "probe-kis-paper-websocket.py"
SPEC = importlib.util.spec_from_file_location("probe_kis_paper_websocket", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


SAFE_SWITCHES = {
    "TRADING_MODE": "paper",
    "LIVE_TRADING_ENABLED": "false",
    "KIS_ENVIRONMENT": "paper",
    "KIS_WEBSOCKET_ENABLED": "true",
    "KIS_PAPER_ORDERS_ENABLED": "false",
    "KIS_REAL_ORDERS_ENABLED": "false",
    "KIS_REAL_RECONCILIATION_ENABLED": "false",
    "KIS_REAL_ACCOUNT_READ_ENABLED": "false",
    "KIS_REAL_EXECUTION_NOTICES_ENABLED": "false",
    "KIS_REAL_MARKET_DATA_ENABLED": "false",
    "KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED": "false",
    "REAL_SIGNAL_MONITOR_ENABLED": "false",
    "REAL_SIGNAL_SUBMISSION_ENABLED": "false",
    "REAL_PROTECTIVE_EXIT_MANAGER_ENABLED": "false",
    "PAPER_AUTOTRADING_ENABLED": "false",
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
    ("KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED", "true"),
    ("REAL_SIGNAL_SUBMISSION_ENABLED", "true"),
])
def test_probe_requires_paper_only_switches_before_requesting_approval(monkeypatch, capsys, unsafe) -> None:
    for key, value in SAFE_SWITCHES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(*unsafe)
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: Path(".env_local"))
    monkeypatch.setattr(probe.KisSettings, "from_environment", classmethod(
        lambda *_args: pytest.fail("must fail before creating KIS settings"),
    ))

    assert probe.main() == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "SAFETY_SWITCHES_NOT_PAPER_READ_ONLY"


@pytest.mark.parametrize(("symbol", "frames", "expected_status"), [
    ("005930",
     ['{"header":{"tr_id":"H0STCNT0"},"body":{"rt_cd":"0","msg_cd":"OK"}}'], "acknowledged_no_tick"),
    ("005930", ["0|H0STCNT0|001|005930^09:30:00^73500"], "tick_received"),
])
def test_probe_subscribes_with_paper_key_and_sends_unsubscribe(
    monkeypatch: pytest.MonkeyPatch,
    symbol: str,
    frames: list[str],
    expected_status: str,
) -> None:
    upstream = FakeWebSocket(frames)
    monkeypatch.setattr(probe, "approval_key", lambda _settings: "sensitive-approval-key")
    monkeypatch.setattr(probe.websockets, "connect", lambda *_args, **_kwargs: FakeConnection(upstream))
    settings = SimpleNamespace()
    stream = SimpleNamespace(websocket_url="ws://paper.invalid:31000")

    result = asyncio.run(probe.run_probe(settings, stream, "KR", symbol, None, 0))

    assert result["status"] == expected_status
    assert result["order_sent"] is False
    assert result["unsubscribe_sent"] is True
    assert upstream.sent[0]["header"]["approval_key"] == "sensitive-approval-key"
    assert upstream.sent[0]["header"]["tr_type"] == "1"
    assert upstream.sent[1]["header"]["tr_type"] == "2"


def test_paper_probe_rejects_us_feed_before_requesting_approval(monkeypatch, capsys) -> None:
    for key, value in SAFE_SWITCHES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: Path(".env_local"))
    monkeypatch.setattr(probe.KisSettings, "from_environment", classmethod(
        lambda *_args: pytest.fail("PAPER US feed must not be attempted"),
    ))

    assert probe.main("US", "AAPL", "NAS") == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "PAPER_PROBE_SUPPORTS_DOMESTIC_ONLY"


def test_probe_does_not_return_raw_rejection_payload_or_mark_it_acknowledged(monkeypatch) -> None:
    upstream = FakeWebSocket([
        '{"header":{"tr_id":"H0STCNT0"},"body":{"rt_cd":"1","msg_cd":"SAFE_CODE","msg1":"private response"}}',
    ])
    monkeypatch.setattr(probe, "approval_key", lambda _settings: "approval")
    monkeypatch.setattr(probe.websockets, "connect", lambda *_args, **_kwargs: FakeConnection(upstream))

    result = asyncio.run(probe.run_probe(
        SimpleNamespace(), SimpleNamespace(websocket_url="ws://paper.invalid"), "KR", "005930", None, 0,
    ))

    assert result["status"] == "rejected"
    assert result["broker_code"] == "SAFE_CODE"
    assert result["acknowledged"] is False
    assert result["unsubscribe_sent"] is False
    assert "private response" not in json.dumps(result)


def test_main_emits_sanitized_result_after_safe_paper_preflight(monkeypatch, capsys) -> None:
    for key, value in SAFE_SWITCHES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("KIS_REAL_APP_KEY", "must-not-reach-probe")
    monkeypatch.setenv("KIS_REAL_APP_SECRET", "must-not-reach-probe")
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: Path(".env_local"))
    monkeypatch.setattr(probe.KisSettings, "from_environment", classmethod(
        lambda _cls, _environment: SimpleNamespace(app_key="private-key", app_secret="private-secret", shared_rate_limit_enabled=True),
    ))
    monkeypatch.setattr(probe.StreamSettings, "from_environment", classmethod(
        lambda _cls, _environment: SimpleNamespace(enabled=True, websocket_url="ws://paper.invalid"),
    ))

    async def fake_probe(*_args: object) -> dict[str, Any]:
        assert probe.os.getenv("KIS_REAL_APP_KEY") == ""
        assert probe.os.getenv("KIS_REAL_APP_SECRET") == ""
        return {
            "status": "acknowledged_no_tick", "environment": "paper", "market": "KR", "symbol": "005930",
            "exchange": None, "acknowledged": True, "tick_received": False,
            "unsubscribe_sent": True, "order_sent": False,
        }

    monkeypatch.setattr(probe, "run_probe", fake_probe)

    assert probe.main() == 0
    output = capsys.readouterr().out
    assert '"status": "acknowledged_no_tick"' in output
    assert '"order_sent": false' in output
    assert "private-key" not in output
    assert "private-secret" not in output
