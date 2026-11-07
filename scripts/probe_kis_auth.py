from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))
sys.path.insert(0, str(repository / "scripts" / "lib"))

from app.kis import KisApiError, KisReadOnlyClient, KisSettings
from kis_local_env import load_local_environment


def safety_check(environment: str) -> bool:
    required = {
        "TRADING_MODE": "paper",
        "LIVE_TRADING_ENABLED": "false",
        "KIS_PAPER_ORDERS_ENABLED": "false",
        "KIS_REAL_ORDERS_ENABLED": "false",
        "PAPER_AUTOTRADING_ENABLED": "false",
        "KIS_REAL_ACCOUNT_READ_ENABLED": "false",
    }
    if any(os.getenv(key, "").strip().lower() != value for key, value in required.items()):
        return False
    if environment == "paper":
        return os.getenv("KIS_ENVIRONMENT", "").strip().lower() == "paper"
    return True


def main(environment: str = "paper") -> int:
    if environment not in {"paper", "real"}:
        print(json.dumps({"status": "blocked", "reason": "UNSUPPORTED_ENVIRONMENT"}))
        return 2
    try:
        source = load_local_environment(repository)
    except (OSError, ValueError):
        print(json.dumps({"status": "blocked", "reason": "LOCAL_ENV_UNAVAILABLE"}))
        return 2
    if not safety_check(environment):
        print(json.dumps({"status": "blocked", "reason": "SAFETY_SWITCHES_NOT_READ_ONLY"}))
        return 2

    settings = KisSettings.from_environment(environment)
    if not settings.app_key or not settings.app_secret or not settings.shared_rate_limit_enabled:
        print(json.dumps({"status": "blocked", "reason": "CREDENTIALS_UNAVAILABLE"}))
        return 2

    client = KisReadOnlyClient(settings)
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "environment": environment,
        "local_config_file": source.name,
        "account_endpoint_called": False,
        "order_or_cancel_sent": False,
    }
    try:
        client._access_token()
        result["status"] = "authenticated"
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except KisApiError as exception:
        result.update({
            "status": "authentication_failed",
            "failure_type": type(exception).__name__,
            "http_status": exception.http_status,
            "broker_code": exception.broker_code,
            "response_content_type": exception.response_content_type,
            "response_bytes": exception.response_bytes,
            "response_fields": list(exception.response_fields),
            "response_error_category": exception.response_error_category,
            "response_error_code": exception.response_error_code,
        })
        print(json.dumps(result, ensure_ascii=False))
        return 3
    except Exception as exception:
        result.update({"status": "authentication_failed", "failure_type": type(exception).__name__})
        print(json.dumps(result, ensure_ascii=False))
        return 3
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KIS OAuth-only diagnostic; never calls account or order endpoints.")
    parser.add_argument("--environment", choices=("paper", "real"), default="paper")
    raise SystemExit(main(parser.parse_args().environment))
