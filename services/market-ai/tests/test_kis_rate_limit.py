from hashlib import sha256

import pytest

from app import kis_rate_limit
from app.kis import KisApiError, KisReadOnlyClient, KisSettings
from app.kis_rate_limit import SharedRateLimitUnavailable


class _Cursor:
    def __init__(self, delay_millis):
        self.delay_millis = delay_millis
        self.statement = None
        self.parameters = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters

    def fetchone(self):
        return (self.delay_millis,)


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


def test_shared_rate_slot_uses_postgres_time_and_app_key_fingerprint(monkeypatch):
    cursor = _Cursor(0)
    monkeypatch.setattr(kis_rate_limit.psycopg, "connect", lambda **_kwargs: _Connection(cursor))

    kis_rate_limit.wait_for_kis_rest_slot("paper", "local-test-app-key", 1.0)

    assert "ON CONFLICT (environment, app_key_sha256) DO UPDATE" in cursor.statement
    assert cursor.parameters == (
        "PAPER", sha256(b"local-test-app-key").hexdigest(), 1.0, 1.0, 1.0
    )
    assert "clock_timestamp()" in cursor.statement
    assert "AS delay_millis" in cursor.statement
    assert "local-test-app-key" not in cursor.statement


def test_shared_rate_slot_waits_only_for_database_computed_delay(monkeypatch):
    cursor = _Cursor(37)
    sleeps = []
    monkeypatch.setattr(kis_rate_limit.psycopg, "connect", lambda **_kwargs: _Connection(cursor))
    monkeypatch.setattr(kis_rate_limit.time, "sleep", sleeps.append)

    kis_rate_limit.wait_for_kis_rest_slot("paper", "local-test-app-key", 1.0)

    assert sleeps == [0.037]


def test_shared_rate_slot_fails_closed_without_database_delay(monkeypatch):
    cursor = _Cursor(None)
    monkeypatch.setattr(kis_rate_limit.psycopg, "connect", lambda **_kwargs: _Connection(cursor))

    with pytest.raises(SharedRateLimitUnavailable, match="shared KIS request pacing is unavailable"):
        kis_rate_limit.wait_for_kis_rest_slot("paper", "local-test-app-key", 1.0)


def test_real_kis_rest_client_enforces_one_second_minimum(monkeypatch):
    settings = KisSettings(True, "real", "real-key", "real-secret", "1234567801",
                           shared_rate_limit_enabled=True)
    client = KisReadOnlyClient(settings)
    observed = []
    monkeypatch.setattr("app.kis.wait_for_kis_rest_slot",
                        lambda environment, account_key, interval: observed.append(interval))

    client._wait_for_rest_slot()

    assert observed == [1.0]
    client.close()


def test_shared_token_slot_uses_separate_one_minute_app_key_fingerprint(monkeypatch):
    cursor = _Cursor(0)
    monkeypatch.setattr(kis_rate_limit.psycopg, "connect", lambda **_kwargs: _Connection(cursor))

    kis_rate_limit.wait_for_kis_token_slot("paper", "local-test-app-key")

    assert "kis_token_issue_rate_limit_slot" in cursor.statement
    assert "interval '1 minute'" in cursor.statement
    assert "clock_timestamp()" in cursor.statement
    assert "AS delay_millis" in cursor.statement
    assert cursor.parameters == ("PAPER", sha256(b"local-test-app-key").hexdigest())
    assert "local-test-app-key" not in cursor.statement


def test_shared_token_slot_waits_only_for_database_computed_delay(monkeypatch):
    cursor = _Cursor(1250)
    sleeps = []
    monkeypatch.setattr(kis_rate_limit.psycopg, "connect", lambda **_kwargs: _Connection(cursor))
    monkeypatch.setattr(kis_rate_limit.time, "sleep", sleeps.append)

    kis_rate_limit.wait_for_kis_token_slot("paper", "local-test-app-key")

    assert sleeps == [1.25]


def test_shared_rate_slot_database_failure_is_reported_without_falling_back(monkeypatch):
    def unavailable(**_kwargs):
        raise kis_rate_limit.psycopg.OperationalError("database unavailable")

    monkeypatch.setattr(kis_rate_limit.psycopg, "connect", unavailable)
    with pytest.raises(SharedRateLimitUnavailable):
        kis_rate_limit.wait_for_kis_rest_slot("real", "local-test-app-key", 0.1)


def test_python_kis_client_fails_closed_if_shared_rate_limit_is_unavailable(monkeypatch):
    settings = KisSettings(True, "paper", "test-key", "test-secret", shared_rate_limit_enabled=True)
    client = KisReadOnlyClient(settings)

    def unavailable(*_args):
        raise SharedRateLimitUnavailable("database unavailable")

    monkeypatch.setattr("app.kis.wait_for_kis_rest_slot", unavailable)
    with pytest.raises(KisApiError, match="shared KIS request pacing is unavailable"):
        client._wait_for_rest_slot()
    client.close()
