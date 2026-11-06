from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))

from app.kis import KisReadOnlyClient, KisSettings


def load_local_environment() -> Path:
    for name in (".env_local", ".env"):
        path = repository / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
                continue
            key, value = trimmed.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)
        return path
    raise ValueError("ignored .env_local or .env file is missing")


def main() -> int:
    try:
        source = load_local_environment()
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
        "transaction_id": "VTTC0084R",
        "local_config_file": source.name,
        "order_or_cancel_sent": False,
    }
    account = settings.account_number
    params = {
        "CANO": account[:8],
        "ACNT_PRDT_CD": account[8:],
        "INQR_DVSN_1": "0",
        "INQR_DVSN_2": "0",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    try:
        headers = {
            "authorization": f"Bearer {client._access_token()}",
            "appkey": settings.app_key,
            "appsecret": settings.app_secret,
            "tr_id": "VTTC0084R",
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
    except Exception:
        result["status"] = "request_failed"
        result["reason"] = "AUTH_OR_READ_ONLY_REQUEST_FAILED"
        print(json.dumps(result, ensure_ascii=False))
        return 3
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
