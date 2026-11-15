from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repository / "services" / "market-ai"))
sys.path.insert(0, str(repository / "scripts" / "lib"))

from app.kis import KisReadOnlyClient, KisSettings
from app.market_calendar import exchange_session_dates
from kis_local_env import load_local_environment

API_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
TRANSACTION_ID = "VTTC0081R"
MAX_PAGES = 5


def _resolve_evidence_path(value: str | None) -> Path | None:
    if value is None:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repository / candidate
    candidate = candidate.resolve()
    allowed_root = (repository / ".local" / "validation").resolve()
    if not candidate.is_relative_to(allowed_root) or candidate.suffix.lower() != ".jsonl":
        raise ValueError("evidence path must be a JSONL file under .local/validation")
    return candidate


def _valid_quantity(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{1,12}", value.strip()):
        return int(value.strip())
    return None


def _emit(result: dict[str, object], evidence_file: Path | None) -> None:
    if evidence_file is not None:
        result["evidence_path"] = evidence_file.relative_to(repository.resolve()).as_posix()
        result["evidence_persisted"] = True
        try:
            evidence_file.parent.mkdir(parents=True, exist_ok=True)
            with evidence_file.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        except OSError as exception:
            result["evidence_persisted"] = False
            result["evidence_write_error"] = type(exception).__name__
    print(json.dumps(result, ensure_ascii=False))


def main(session_date: str, evidence_path: str | None = None) -> int:
    try:
        requested_date = date.fromisoformat(session_date)
        evidence_file = _resolve_evidence_path(evidence_path)
    except (OSError, ValueError):
        _emit({"status": "blocked", "reason": "INVALID_INPUT"}, None)
        return 2

    try:
        source = load_local_environment(repository)
    except (OSError, ValueError):
        _emit({"status": "blocked", "reason": "LOCAL_ENV_UNAVAILABLE"}, evidence_file)
        return 2

    expected = {
        "TRADING_MODE": "paper", "LIVE_TRADING_ENABLED": "false",
        "PAPER_AUTOTRADING_ENABLED": "false", "MARKET_OPERATIONAL_SCOPE": "kr",
        "KIS_ENVIRONMENT": "paper", "KIS_ACCOUNT_READ_ENABLED": "true",
        "KIS_PAPER_ACCOUNT_READ_ENABLED": "true", "KIS_REAL_ACCOUNT_READ_ENABLED": "false",
        "KIS_PAPER_ORDERS_ENABLED": "false", "KIS_REAL_ORDERS_ENABLED": "false",
        "KIS_REAL_RECONCILIATION_ENABLED": "false",
    }
    if any(os.getenv(key, "").strip().lower() != value for key, value in expected.items()):
        _emit({"status": "blocked", "reason": "SAFETY_SWITCHES_NOT_READ_ONLY"}, evidence_file)
        return 2

    local_today = datetime.now(timezone(timedelta(hours=9))).date()
    if requested_date > local_today or requested_date < local_today - timedelta(days=90):
        _emit({"status": "blocked", "reason": "SESSION_DATE_OUT_OF_RANGE"}, evidence_file)
        return 2
    try:
        verified_sessions = exchange_session_dates("KR", requested_date, requested_date)
    except (RuntimeError, ValueError):
        _emit({"status": "blocked", "reason": "KRX_CALENDAR_UNAVAILABLE"}, evidence_file)
        return 2
    if requested_date not in verified_sessions:
        _emit({"status": "blocked", "reason": "NOT_A_KRX_SESSION"}, evidence_file)
        return 2

    settings = KisSettings.from_environment("paper")
    if (not settings.app_key or not settings.app_secret or len(settings.account_number) != 10
            or not settings.shared_rate_limit_enabled):
        _emit({"status": "blocked", "reason": "PAPER_CREDENTIALS_UNAVAILABLE"}, evidence_file)
        return 2

    result: dict[str, object] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "environment": "paper", "session_date": requested_date.isoformat(),
        "api_path": API_PATH, "transaction_id": TRANSACTION_ID,
        "local_config_file": source.name, "request_count": 0,
        "returned_order_count": 0, "remaining_quantity_fields_observed": False,
        "positive_remaining_quantity_rows": 0, "all_pages_read": False,
        "cancel_preflight_verified": False, "order_or_cancel_sent": False,
    }
    account = settings.account_number
    params = {
        "CANO": account[:8], "ACNT_PRDT_CD": account[8:],
        "INQR_STRT_DT": requested_date.strftime("%Y%m%d"),
        "INQR_END_DT": requested_date.strftime("%Y%m%d"),
        "SLL_BUY_DVSN_CD": "00", "PDNO": "", "CCLD_DVSN": "00",
        "INQR_DVSN": "00", "INQR_DVSN_3": "00", "ORD_GNO_BRNO": "",
        "ODNO": "", "INQR_DVSN_1": "", "EXCG_ID_DVSN_CD": "KRX",
        "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
    }
    client = KisReadOnlyClient(settings)
    seen_cursors: set[tuple[str, str]] = set()
    continuation = ""
    try:
        headers = {
            "authorization": f"Bearer {client._access_token()}",
            "appkey": settings.app_key, "appsecret": settings.app_secret,
            "tr_id": TRANSACTION_ID, "custtype": "P",
        }
        for _ in range(MAX_PAGES):
            response = client._get(
                f"{settings.base_url}{API_PATH}", headers={**headers, "tr_cont": continuation},
                params=params,
            )
            result["request_count"] = int(result["request_count"]) + 1
            try:
                payload = response.json()
            except (ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            result["http_status"] = response.status_code
            code = payload.get("msg_cd")
            broker_code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", code) else None
            result["broker_code"] = broker_code
            rt_code = payload.get("rt_cd")
            result["broker_result_code"] = rt_code if isinstance(rt_code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", rt_code) else None
            if response.status_code >= 400 or rt_code != "0":
                result["status"] = "broker_error"
                break
            rows = payload.get("output1")
            if isinstance(rows, list):
                result["returned_order_count"] = int(result["returned_order_count"]) + len(rows)
                valid_remaining = [_valid_quantity(row.get("rmn_qty")) for row in rows if isinstance(row, dict)]
                result["remaining_quantity_fields_observed"] = bool(result["remaining_quantity_fields_observed"]) or any(value is not None for value in valid_remaining)
                result["positive_remaining_quantity_rows"] = int(result["positive_remaining_quantity_rows"]) + sum(value is not None and value > 0 for value in valid_remaining)
            header_continuation = response.headers.get("tr_cont", "")
            if header_continuation not in {"M", "F"}:
                result["all_pages_read"] = True
                result["status"] = "read_complete"
                break
            next_search = str(payload.get("ctx_area_fk100") or "")
            next_key = str(payload.get("ctx_area_nk100") or "")
            cursor = (next_search, next_key)
            if not all(cursor) or cursor in seen_cursors:
                result["status"] = "pagination_incomplete"
                break
            seen_cursors.add(cursor)
            params["CTX_AREA_FK100"], params["CTX_AREA_NK100"] = cursor
            continuation = "N"
        else:
            result["status"] = "page_limit_reached"
        _emit(result, evidence_file)
        return 0 if result.get("status") == "read_complete" else 3
    except Exception as exception:
        result["status"] = "request_failed"
        result["failure_type"] = type(exception).__name__
        _emit(result, evidence_file)
        return 3
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read-only KIS PAPER domestic order-history probe.")
    parser.add_argument("--session-date", required=True, help="Korea-market session date, YYYY-MM-DD")
    parser.add_argument("--evidence-path", help="append sanitized JSON to .local/validation/*.jsonl")
    arguments = parser.parse_args()
    raise SystemExit(main(arguments.session_date, arguments.evidence_path))
