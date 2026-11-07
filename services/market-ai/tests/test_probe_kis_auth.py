import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import scripts.probe_kis_auth as probe
from app.kis import KisApiError


def test_local_environment_file_overrides_inherited_value_and_prefers_local(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env_local").write_text('KIS_PAPER_APP_KEY="local-key"\n', encoding="utf-8")
    (tmp_path / ".env").write_text("KIS_PAPER_APP_KEY=other-key\n", encoding="utf-8")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "inherited-key")
    monkeypatch.setenv("KIS_REAL_APP_KEY", "stale-inherited-key")

    loaded = probe.load_local_environment(tmp_path)

    assert loaded.name == ".env_local"
    assert os.environ["KIS_PAPER_APP_KEY"] == "local-key"
    assert "KIS_REAL_APP_KEY" not in os.environ


@pytest.mark.parametrize("environment", ["paper", "real"])
def test_auth_only_probe_does_not_call_account_or_show_token(
    tmp_path: Path, monkeypatch, capsys, environment: str,
) -> None:
    (tmp_path / ".env_local").write_text("TRADING_MODE=paper\n", encoding="utf-8")
    monkeypatch.setattr(probe, "repository", tmp_path)
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: tmp_path / ".env_local")
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("KIS_PAPER_ORDERS_ENABLED", "false")
    monkeypatch.setenv("KIS_REAL_ORDERS_ENABLED", "false")
    monkeypatch.setenv("PAPER_AUTOTRADING_ENABLED", "false")
    monkeypatch.setenv("KIS_REAL_ACCOUNT_READ_ENABLED", "false")
    monkeypatch.setenv("KIS_ENVIRONMENT", "paper")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "paper-key")
    monkeypatch.setenv("KIS_PAPER_APP_SECRET", "paper-secret")
    monkeypatch.setenv("KIS_REAL_APP_KEY", "real-key")
    monkeypatch.setenv("KIS_REAL_APP_SECRET", "real-secret")
    monkeypatch.setenv("KIS_SHARED_RATE_LIMIT_ENABLED", "true")

    class FakeClient:
        def __init__(self, settings):
            assert settings.environment == environment
            self.closed = False

        def _access_token(self):
            return "sensitive-token"

        def get_account_balance(self, *args, **kwargs):
            raise AssertionError("auth-only probe must never call an account endpoint")

        def close(self):
            self.closed = True

    monkeypatch.setattr(probe, "KisReadOnlyClient", FakeClient)

    assert probe.main(environment) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "authenticated"
    assert output["environment"] == environment
    assert output["account_endpoint_called"] is False
    assert output["order_or_cancel_sent"] is False
    assert "sensitive-token" not in json.dumps(output)


def test_auth_only_probe_reports_safe_oauth_error_metadata(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / ".env_local").write_text("TRADING_MODE=paper\n", encoding="utf-8")
    monkeypatch.setattr(probe, "repository", tmp_path)
    monkeypatch.setattr(probe, "load_local_environment", lambda _repository: tmp_path / ".env_local")
    for key, value in {
        "TRADING_MODE": "paper", "LIVE_TRADING_ENABLED": "false",
        "KIS_PAPER_ORDERS_ENABLED": "false", "KIS_REAL_ORDERS_ENABLED": "false",
        "PAPER_AUTOTRADING_ENABLED": "false", "KIS_REAL_ACCOUNT_READ_ENABLED": "false",
        "KIS_ENVIRONMENT": "paper", "KIS_PAPER_APP_KEY": "paper-key",
        "KIS_PAPER_APP_SECRET": "paper-secret", "KIS_SHARED_RATE_LIMIT_ENABLED": "true",
    }.items():
        monkeypatch.setenv(key, value)

    class FakeClient:
        def __init__(self, settings):
            pass

        def _access_token(self):
            raise KisApiError(
                "KIS authentication request failed (HTTP 403, code unknown).",
                http_status=403, broker_code="unknown", response_content_type="application/json",
                response_bytes=84, response_fields=("error_code", "error_description"),
                response_error_category="broker_server_error", response_error_code="EGW00002",
            )

        def close(self):
            pass

    monkeypatch.setattr(probe, "KisReadOnlyClient", FakeClient)

    assert probe.main("paper") == 3
    output = json.loads(capsys.readouterr().out)
    assert output["http_status"] == 403
    assert output["response_error_code"] == "EGW00002"
    assert output["response_error_category"] == "broker_server_error"
    assert output["account_endpoint_called"] is False
