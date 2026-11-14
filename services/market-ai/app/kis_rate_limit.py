"""PostgreSQL-backed KIS REST pacing shared with the Spring service."""

from __future__ import annotations

import hashlib
import os
import time

import psycopg


class SharedRateLimitUnavailable(RuntimeError):
    """The shared KIS request slot could not be reserved from PostgreSQL."""


_RESERVE_SLOT_SQL = """INSERT INTO kis_rest_rate_limit_slot
    (environment, app_key_sha256, next_request_at)
VALUES (%s, %s, clock_timestamp() + interval '1 second' * %s)
ON CONFLICT (environment, app_key_sha256) DO UPDATE
SET next_request_at = GREATEST(kis_rest_rate_limit_slot.next_request_at, clock_timestamp())
                       + interval '1 second' * %s,
    updated_at = clock_timestamp()
RETURNING GREATEST(0, CEIL(EXTRACT(EPOCH FROM
    ((next_request_at - interval '1 second' * %s) - clock_timestamp())) * 1000)::bigint)
    AS delay_millis"""
_RESERVE_TOKEN_SLOT_SQL = """INSERT INTO kis_token_issue_rate_limit_slot
    (environment, app_key_sha256, next_request_at)
VALUES (%s, %s, clock_timestamp() + interval '1 minute')
ON CONFLICT (environment, app_key_sha256) DO UPDATE
SET next_request_at = GREATEST(kis_token_issue_rate_limit_slot.next_request_at, clock_timestamp())
                       + interval '1 minute',
    updated_at = clock_timestamp()
RETURNING GREATEST(0, CEIL(EXTRACT(EPOCH FROM
    ((next_request_at - interval '1 minute') - clock_timestamp())) * 1000)::bigint)
    AS delay_millis"""


def wait_for_kis_rest_slot(environment: str, app_key: str, minimum_interval_seconds: float) -> None:
    """Reserve a shared slot, commit it, then wait until its start time."""
    if environment not in {"paper", "real"} or not app_key or not 0 < minimum_interval_seconds <= 10:
        raise ValueError("valid KIS rate limit inputs are required")
    fingerprint = hashlib.sha256(app_key.encode("utf-8")).hexdigest()
    try:
        with psycopg.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", "5432"),
            dbname=os.getenv("POSTGRES_DB", "ai_stock_trader"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            connect_timeout=3,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    _RESERVE_SLOT_SQL,
                    (environment.upper(), fingerprint, minimum_interval_seconds,
                     minimum_interval_seconds, minimum_interval_seconds),
                )
                delay_millis = cursor.fetchone()[0]
    except psycopg.Error as exc:
        raise SharedRateLimitUnavailable("shared KIS request pacing is unavailable") from exc
    if delay_millis is None:
        raise SharedRateLimitUnavailable("shared KIS request pacing is unavailable")
    if delay_millis > 0:
        time.sleep(delay_millis / 1000)


def wait_for_kis_token_slot(environment: str, app_key: str) -> None:
    """Reserve a conservative one-per-minute token issue slot shared with Spring."""
    if environment not in {"paper", "real"} or not app_key:
        raise ValueError("valid KIS token pacing inputs are required")
    fingerprint = hashlib.sha256(app_key.encode("utf-8")).hexdigest()
    try:
        with psycopg.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=os.getenv("POSTGRES_PORT", "5432"),
            dbname=os.getenv("POSTGRES_DB", "ai_stock_trader"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            connect_timeout=3,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(_RESERVE_TOKEN_SLOT_SQL, (environment.upper(), fingerprint))
                delay_millis = cursor.fetchone()[0]
    except psycopg.Error as exc:
        raise SharedRateLimitUnavailable("shared KIS token pacing is unavailable") from exc
    if delay_millis is None:
        raise SharedRateLimitUnavailable("shared KIS token pacing is unavailable")
    if delay_millis > 0:
        time.sleep(delay_millis / 1000)
