from __future__ import annotations

import hashlib
import os
from time import monotonic
from uuid import uuid4

import psycopg
import pytest

from app.kis_rate_limit import wait_for_kis_rest_slot, wait_for_kis_token_slot


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LOCAL_POSTGRES_INTEGRATION_TESTS", "false").strip().lower() != "true",
    reason="local PostgreSQL integration tests are opt-in",
)


def _connection_args() -> dict[str, object]:
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "dbname": os.getenv("POSTGRES_DB", "ai_stock_trader"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
        "connect_timeout": 3,
    }


def test_python_rate_limiter_reserves_serialized_slots_using_postgres_clock() -> None:
    app_key = f"python-rate-limit-integration-{uuid4()}"
    token_app_key = f"python-token-rate-limit-integration-{uuid4()}"
    fingerprint = hashlib.sha256(app_key.encode("utf-8")).hexdigest()
    token_fingerprint = hashlib.sha256(token_app_key.encode("utf-8")).hexdigest()
    connection_args = _connection_args()
    with psycopg.connect(**connection_args) as connection:
        connection.execute(
            "DELETE FROM kis_rest_rate_limit_slot WHERE environment = 'PAPER' AND app_key_sha256 = %s",
            (fingerprint,),
        )
        connection.execute(
            "DELETE FROM kis_token_issue_rate_limit_slot WHERE environment = 'PAPER' AND app_key_sha256 = %s",
            (token_fingerprint,),
        )
    try:
        started = monotonic()
        wait_for_kis_rest_slot("paper", app_key, 0.5)
        wait_for_kis_rest_slot("paper", app_key, 0.5)
        elapsed = monotonic() - started

        with psycopg.connect(**connection_args) as connection:
            row = connection.execute(
                """SELECT app_key_sha256, next_request_at IS NOT NULL
                     FROM kis_rest_rate_limit_slot
                    WHERE environment = 'PAPER' AND app_key_sha256 = %s""",
                (fingerprint,),
            ).fetchone()

        assert elapsed >= 0.45
        assert row == (fingerprint, True)
        wait_for_kis_token_slot("paper", token_app_key)
        with psycopg.connect(**connection_args) as connection:
            token_row = connection.execute(
                """SELECT app_key_sha256, next_request_at IS NOT NULL
                     FROM kis_token_issue_rate_limit_slot
                    WHERE environment = 'PAPER' AND app_key_sha256 = %s""",
                (token_fingerprint,),
            ).fetchone()
        assert token_row == (token_fingerprint, True)
    finally:
        with psycopg.connect(**connection_args) as connection:
            connection.execute(
                "DELETE FROM kis_rest_rate_limit_slot WHERE environment = 'PAPER' AND app_key_sha256 = %s",
                (fingerprint,),
            )
            connection.execute(
                "DELETE FROM kis_token_issue_rate_limit_slot WHERE environment = 'PAPER' AND app_key_sha256 = %s",
                (token_fingerprint,),
            )
