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

OFFICIAL_TRANSACTION_IDS = {"TTTC0084R", "TTTC8036R"}


def _has_valid_cancel_quantity(rows: object) -> bool:
    candidates = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []
    for row in candidates:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if not isinstance(key, str) or key.lower() != "psbl_qty" or isinstance(value, bool):
                continue
            if isinstance(value, int) and value >= 0:
                return True
            if isinstance(value, str) and re.fullmatch(r"\d{1,12}", value.strip()):
                return True
    return False


def _has_positive_cancel_quantity(rows: object) -> bool:
    candidates = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []
    for row in candidates:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if not isinstance(key, str) or key.lower() != "psbl_qty" or isinstance(value, bool):
                continue
            if isinstance(value, int) and value > 0:
                return True
            if isinstance(value, str) and re.fullmatch(r"\d{1,12}", value.strip()):
                if int(value.strip()) > 0:
                    return True
    return False


def main(transaction_id: str = "TTTC0084R") -> int:
    # The current official sample uses TTTC0084R. The official legacy sample
    # uses TTTC8036R and its auth helper maps T-prefixed IDs to V-prefixed IDs
    # for PAPER. The legacy example does not document psbl_qty, so success alone
    # must never unlock cancellation.
    if transaction_id not in OFFICIAL_TRANSACTION_IDS:
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
    request_transaction_id = "VTTC8036R" if transaction_id == "TTTC8036R" else transaction_id
    query_kind = "legacy_order_sequence" if transaction_id == "TTTC8036R" else "current_item_sequence"
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "environment": "paper",
        "api_path": "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
        "transaction_id": transaction_id,
        "request_transaction_id": request_transaction_id,
        "query_kind": query_kind,
        "local_config_file": source.name,
        "order_or_cancel_sent": False,
    }
    account = settings.account_number
    params = {
        "CANO": account[:8],
        "ACNT_PRDT_CD": account[8:],
        # The current official sample describes 0=order sequence, 1=item sequence
        # and demonstrates item sequence for this cancelable-order lookup.
        "INQR_DVSN_1": "0" if query_kind == "legacy_order_sequence" else "1",
        "INQR_DVSN_2": "0",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    try:
        headers = {
            "authorization": f"Bearer {client._access_token()}",
            "appkey": settings.app_key,
            "appsecret": settings.app_secret,
            "tr_id": request_transaction_id,
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
        result["returned_row_count"] = len(rows) if isinstance(rows, list) else (1 if isinstance(rows, dict) else 0)
        result["cancel_quantity_field_observed"] = _has_valid_cancel_quantity(rows)
        result["positive_cancel_quantity_available"] = _has_positive_cancel_quantity(rows)
        result["status"] = (
            "valid_preflight_response" if successful and result["positive_cancel_quantity_available"] else
            "no_cancelable_quantity" if successful and result["cancel_quantity_field_observed"] else
            "response_incomplete" if successful else
            "temporary_server_error" if result["broker_code"] == "EGW00002" else
            "broker_error_unclassified"
        )
        if result["status"] == "temporary_server_error":
            result["reason"] = "TEMPORARY_BROKER_SERVER_ERROR"
        elif result["status"] == "broker_error_unclassified":
            result["reason"] = "BROKER_ERROR_NOT_CLASSIFIED"
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "valid_preflight_response" else 3
    except Exception as exception:
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
        if result.get("broker_code") == "EGW00002":
            result["status"] = "temporary_server_error"
            result["reason"] = "TEMPORARY_BROKER_SERVER_ERROR"
        elif result.get("broker_code"):
            result["status"] = "broker_error_unclassified"
            result["reason"] = "BROKER_ERROR_NOT_CLASSIFIED"
        else:
            result["status"] = "request_failed"
            result["reason"] = "AUTH_OR_READ_ONLY_REQUEST_FAILED"
        print(json.dumps(result, ensure_ascii=False))
        return 3
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only PAPER cancel-preflight service probe.")
    parser.add_argument("--transaction-id", choices=tuple(sorted(OFFICIAL_TRANSACTION_IDS)), default="TTTC0084R")
    raise SystemExit(main(parser.parse_args().transaction_id))
