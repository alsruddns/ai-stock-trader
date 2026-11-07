from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))
sys.path.insert(0, str(repository / "scripts" / "lib"))

from app.kis import KisApiError, KisReadOnlyClient, KisSettings
from kis_local_env import load_local_environment


def main(transaction_id: str = "TTTC0084R") -> int:
    # The official sample documents only TTTC0084R. The official PAPER matrix
    # does not mark this inquiry as supported, so do not guess PAPER TR IDs.
    if transaction_id != "TTTC0084R":
        print(json.dumps({"status": "blocked", "reason": "UNSUPPORTED_TRANSACTION_ID"}))
        return 2
    try:
        source = load_local_environment(repository)
    except (OSError, ValueError):
        print(json.dumps({"status": "blocked", "reason": "LOCAL_ENV_UNAVAILABLE"}))
        return 2

    expected = {
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
    if any(os.getenv(key, "").strip().lower() != value for key, value in expected.items()):
        print(json.dumps({"status": "blocked", "reason": "SAFETY_SWITCHES_NOT_READ_ONLY"}))
        return 2

    settings = KisSettings.from_environment("paper")
    if (not settings.app_key or not settings.app_secret or len(settings.account_number) != 10
            or not settings.shared_rate_limit_enabled):
        print(json.dumps({"status": "blocked", "reason": "PAPER_CREDENTIALS_UNAVAILABLE"}))
        return 2

    client = KisReadOnlyClient(settings)
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "environment": "paper",
        "api_path": "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
        "transaction_id": transaction_id,
        "local_config_file": source.name,
        "order_or_cancel_sent": False,
    }
    account = settings.account_number
    params = {
        "CANO": account[:8],
        "ACNT_PRDT_CD": account[8:],
        # The current official sample describes 0=order sequence, 1=item sequence
        # and demonstrates item sequence for this cancelable-order lookup.
        "INQR_DVSN_1": "1",
        "INQR_DVSN_2": "0",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    try:
        headers = {
            "authorization": f"Bearer {client._access_token()}",
            "appkey": settings.app_key,
            "appsecret": settings.app_secret,
            "tr_id": transaction_id,
            "custtype": "P",
        }
        response = client._get(
            f"{settings.base_url}{result['api_path']}", headers=headers, params=params,
        )
        result["http_status"] = response.status_code
        try:
            payload = response.json()
        except (ValueError, TypeError):
            payload = {}
        broker_code = payload.get("msg_cd") if isinstance(payload, dict) else None
        result["broker_code"] = broker_code if isinstance(broker_code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", broker_code) else None
        broker_result_code = payload.get("rt_cd") if isinstance(payload, dict) else None
        result["broker_result_code"] = broker_result_code if isinstance(broker_result_code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", broker_result_code) else None
        successful = response.status_code < 400 and isinstance(payload, dict) and payload.get("rt_cd") == "0"
        rows = payload.get("output") if isinstance(payload, dict) else None
        result["status"] = "supported" if successful else "unsupported_or_rejected"
        result["returned_row_count"] = len(rows) if isinstance(rows, list) else (1 if isinstance(rows, dict) else 0)
        result["cancel_quantity_field_observed"] = (
            any(isinstance(row, dict) and ("psbl_qty" in row or "PSBL_QTY" in row) for row in rows)
            if isinstance(rows, list) else
            isinstance(rows, dict) and ("psbl_qty" in rows or "PSBL_QTY" in rows)
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if successful else 3
    except Exception as exception:
        result["status"] = "request_failed"
        result["reason"] = "AUTH_OR_READ_ONLY_REQUEST_FAILED"
        # The exception class is useful for distinguishing transport, HTTP, and
        # shared-rate-limit failures without emitting the exception text or secrets.
        result["failure_type"] = type(exception).__name__
        safe_http_error = re.fullmatch(
            r"KIS [A-Za-z -]+ request failed \(HTTP (\d{3}), code ([A-Za-z0-9_-]{1,32})\)\.",
            str(exception),
        )
        if safe_http_error:
            result["http_status"] = int(safe_http_error.group(1))
            result["broker_code"] = safe_http_error.group(2)
        if isinstance(exception, KisApiError):
            result["http_status"] = exception.http_status or result.get("http_status")
            result["broker_code"] = exception.broker_code or result.get("broker_code")
            result["response_content_type"] = exception.response_content_type
            result["response_bytes"] = exception.response_bytes
            result["response_fields"] = list(exception.response_fields)
            result["response_error_category"] = exception.response_error_category
            result["response_error_code"] = exception.response_error_code
        print(json.dumps(result, ensure_ascii=False))
        return 3
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only PAPER cancel-preflight service probe.")
    parser.add_argument("--transaction-id", choices=("TTTC0084R",), default="TTTC0084R")
    raise SystemExit(main(parser.parse_args().transaction_id))
