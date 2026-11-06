"""Read-only KIS REST quote and account client. No order APIs are implemented."""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from .kis_rate_limit import (
    SharedRateLimitUnavailable,
    wait_for_kis_rest_slot,
    wait_for_kis_token_slot,
)

Market = Literal["KR", "US"]
Exchange = Literal["NAS", "NYS", "AMS"]

REAL_URL = "https://openapi.koreainvestment.com:9443"
PAPER_URL = "https://openapivts.koreainvestment.com:29443"
DOMESTIC_QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
OVERSEAS_QUOTE_PATH = "/uapi/overseas-price/v1/quotations/price"
DOMESTIC_MINUTE_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
OVERSEAS_MINUTE_CHART_PATH = "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"


class KisApiError(RuntimeError):
    """Safe, non-sensitive error intended for application logging/handling."""


class KisSharedRateLimitUnavailable(KisApiError):
    """KIS requests are blocked because PostgreSQL cannot reserve a shared slot."""


class KisRateLimitError(KisApiError):
    """KIS REST request quota was reached; callers should wait before retrying."""


def _ensure_kis_http_success(response: httpx.Response, operation: str) -> None:
    if response.status_code < 400:
        return
    code = "unknown"
    try:
        payload = response.json()
        candidate = payload.get("msg_cd") if isinstance(payload, dict) else None
        if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", candidate):
            code = candidate
    except (ValueError, TypeError):
        pass
    if response.status_code == 429 or code == "EGW00201":
        raise KisRateLimitError("KIS rate limit reached; wait before requesting again.")
    raise KisApiError(f"KIS {operation} request failed (HTTP {response.status_code}, code {code}).")


@dataclass(frozen=True)
class KisSettings:
    enabled: bool
    environment: Literal["paper", "real"]
    app_key: str
    app_secret: str
    account_number: str = ""
    shared_rate_limit_enabled: bool = False

    def __repr__(self) -> str:
        account_hint = f"{'*' * max(0, len(self.account_number) - 2)}{self.account_number[-2:]}" if self.account_number else ""
        return (
            "KisSettings("
            f"enabled={self.enabled!r}, environment={self.environment!r}, "
            f"credentials_configured={bool(self.app_key and self.app_secret)!r}, "
            f"account_number={account_hint!r}, "
            f"shared_rate_limit_enabled={self.shared_rate_limit_enabled!r})"
        )

    @classmethod
    def from_environment(cls, environment: Literal["paper", "real"] | None = None) -> "KisSettings":
        mode = (environment or os.getenv("KIS_ENVIRONMENT", "paper")).strip().lower()
        if mode not in {"paper", "real"}:
            raise ValueError("KIS_ENVIRONMENT must be paper or real")
        suffix = mode.upper()
        return cls(
            enabled=os.getenv("KIS_MARKET_DATA_ENABLED", "false").strip().lower() == "true",
            environment=mode,  # type: ignore[arg-type]
            app_key=os.getenv(f"KIS_{suffix}_APP_KEY", "").strip(),
            app_secret=os.getenv(f"KIS_{suffix}_APP_SECRET", "").strip(),
            account_number=os.getenv(f"KIS_{suffix}_ACCOUNT_NUMBER", "").strip().replace("-", ""),
            shared_rate_limit_enabled=os.getenv("KIS_SHARED_RATE_LIMIT_ENABLED", "true").strip().lower() == "true",
        )

    @property
    def base_url(self) -> str:
        return PAPER_URL if self.environment == "paper" else REAL_URL


@dataclass(frozen=True)
class Quote:
    market: Market
    symbol: str
    exchange: str | None
    currency: str
    price: Decimal
    change: Decimal | None
    change_percent: Decimal | None
    volume: int | None
    retrieved_at: datetime
    source: str = "KIS_REST_SNAPSHOT"


@dataclass(frozen=True)
class AccountBalance:
    environment: Literal["paper", "real"]
    market: Market
    currency: str
    summary: dict[str, Any]
    positions: list[dict[str, Any]]
    retrieved_at: datetime
    source: str = "KIS_REST_ACCOUNT_SNAPSHOT"


def _decimal(value: Any, field: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise KisApiError(f"KIS returned an invalid {field} value") from exc
    if not parsed.is_finite():
        raise KisApiError(f"KIS returned an invalid {field} value")
    return parsed


def _volume(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise KisApiError("KIS returned an invalid volume value") from exc


class KisReadOnlyClient:
    """Small blocking HTTP client for one-shot market quote snapshots."""

    def __init__(self, settings: KisSettings, *, http_client: httpx.Client | None = None):
        if not settings.app_key or not settings.app_secret:
            raise ValueError("KIS app key and secret are required")
        self.settings = settings
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0))
        default_interval = 1.0
        configured_interval = os.getenv(f"KIS_{settings.environment.upper()}_REST_MIN_INTERVAL_SECONDS", "").strip()
        try:
            requested_interval = float(configured_interval) if configured_interval else default_interval
        except ValueError as exc:
            raise ValueError("KIS REST minimum request interval must be numeric seconds") from exc
        if not 0 < requested_interval <= 10:
            raise ValueError("KIS REST minimum request interval must be greater than zero and at most 10 seconds")
        self._rest_min_interval = max(default_interval, requested_interval)
        self._rest_slot_lock = threading.Lock()
        self._next_rest_slot = 0.0
        self._next_token_slot = 0.0
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._token_lock = threading.Lock()

    def _wait_for_rest_slot(self) -> None:
        if self.settings.shared_rate_limit_enabled:
            try:
                wait_for_kis_rest_slot(self.settings.environment, self.settings.app_key, self._rest_min_interval)
            except SharedRateLimitUnavailable as exc:
                raise KisSharedRateLimitUnavailable("shared KIS request pacing is unavailable") from exc
            return
        with self._rest_slot_lock:
            now = time.monotonic()
            slot = max(now, self._next_rest_slot)
            delay = slot - now
            if delay > 0:
                time.sleep(delay)
            self._next_rest_slot = slot + self._rest_min_interval

    def _get(self, url: str, *, headers: dict[str, str], params: dict[str, str]) -> httpx.Response:
        self._wait_for_rest_slot()
        return self._http.get(url, headers=headers, params=params)

    def _wait_for_token_slot(self) -> None:
        if self.settings.shared_rate_limit_enabled:
            try:
                wait_for_kis_token_slot(self.settings.environment, self.settings.app_key)
            except SharedRateLimitUnavailable as exc:
                raise KisSharedRateLimitUnavailable("shared KIS token pacing is unavailable") from exc
            return
        with self._rest_slot_lock:
            now = time.monotonic()
            slot = max(now, self._next_token_slot)
            delay = slot - now
            if delay > 0:
                time.sleep(delay)
            self._next_token_slot = slot + 60.0

    def _access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token and self._token_expires_at and now + timedelta(minutes=5) < self._token_expires_at:
            return self._token

        with self._token_lock:
            now = datetime.now(timezone.utc)
            if self._token and self._token_expires_at and now + timedelta(minutes=5) < self._token_expires_at:
                return self._token
            try:
                self._wait_for_token_slot()
                response = self._http.post(
                    f"{self.settings.base_url}/oauth2/tokenP",
                    json={
                        "grant_type": "client_credentials",
                        "appkey": self.settings.app_key,
                        "appsecret": self.settings.app_secret,
                    },
                    headers={"content-type": "application/json"},
                )
                _ensure_kis_http_success(response, "authentication")
                payload = response.json()
                token = payload.get("access_token")
                expiry = payload.get("access_token_token_expired")
                if not token or not expiry:
                    raise KisApiError("KIS token response was missing required fields")
                # KIS token expiry strings are supplied in Korea local time (UTC+09:00).
                local_expiry = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone(timedelta(hours=9))
                )
                self._token = token
                self._token_expires_at = local_expiry.astimezone(timezone.utc)
                return token
            except KisApiError:
                raise
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                raise KisApiError("KIS authentication failed; check credentials and environment") from exc

    def get_quote(self, market: Market, symbol: str, exchange: Exchange | None = None) -> Quote:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("symbol is required")
        if market == "KR":
            if exchange is not None:
                raise ValueError("exchange is only used for US symbols")
            if not (normalized_symbol.isdigit() and len(normalized_symbol) == 6):
                raise ValueError("KR symbols must be six digits")
            path = DOMESTIC_QUOTE_PATH
            tr_id = "FHKST01010100"
            params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": normalized_symbol}
        elif market == "US":
            if exchange is None:
                raise ValueError("US quotes require exchange NAS, NYS, or AMS")
            path = OVERSEAS_QUOTE_PATH
            tr_id = "HHDFS00000300"
            params = {"AUTH": "", "EXCD": exchange, "SYMB": normalized_symbol}
        else:
            raise ValueError("market must be KR or US")

        try:
            response = self._get(
                f"{self.settings.base_url}{path}",
                headers={
                    "authorization": f"Bearer {self._access_token()}",
                    "appkey": self.settings.app_key,
                    "appsecret": self.settings.app_secret,
                    "tr_id": tr_id,
                    "custtype": "P",
                },
                params=params,
            )
            _ensure_kis_http_success(response, "quote")
            payload = response.json()
        except KisApiError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise KisApiError("KIS quote request failed") from exc

        if payload.get("rt_cd") != "0":
            code = str(payload.get("msg_cd", "unknown"))[:32]
            raise KisApiError(f"KIS rejected the quote request (code {code})")
        output = payload.get("output")
        if not isinstance(output, dict):
            raise KisApiError("KIS quote response did not contain an output object")

        if market == "KR":
            price = _decimal(output.get("stck_prpr"), "price")
            currency = "KRW"
            change = _decimal(output.get("prdy_vrss"), "change")
            change_percent = _decimal(output.get("prdy_ctrt"), "change percent")
            volume = _volume(output.get("acml_vol"))
        else:
            price = _decimal(output.get("last"), "price")
            currency = "USD"
            change = _decimal(output.get("diff"), "change")
            change_percent = _decimal(output.get("rate"), "change percent")
            volume = _volume(output.get("tvol"))
        if price is None or price <= 0:
            raise KisApiError("KIS quote response did not contain a valid price")
        return Quote(
            market=market,
            symbol=normalized_symbol,
            exchange=exchange,
            currency=currency,
            price=price,
            change=change,
            change_percent=change_percent,
            volume=volume,
            retrieved_at=datetime.now(timezone.utc),
        )

    def get_domestic_minute_bars(self, symbol: str, session_date: date, end_time: str = "153000") -> list["MinuteBar"]:
        """Fetch one bounded page of KRX minute bars; KIS does not provide historical spread here."""
        from .quant import MinuteBar

        normalized_symbol = symbol.strip()
        if not (normalized_symbol.isdigit() and len(normalized_symbol) == 6):
            raise ValueError("KR symbols must be six digits")
        if (len(end_time) != 6 or not end_time.isdigit() or int(end_time[:2]) > 23
                or int(end_time[2:4]) > 59 or int(end_time[4:]) > 59):
            raise ValueError("end_time must use HHMMSS")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": normalized_symbol,
            "FID_INPUT_HOUR_1": end_time, "FID_INPUT_DATE_1": session_date.strftime("%Y%m%d"),
            "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": "",
        }
        try:
            response = self._get(
                f"{self.settings.base_url}{DOMESTIC_MINUTE_CHART_PATH}",
                headers={
                    "authorization": f"Bearer {self._access_token()}", "appkey": self.settings.app_key,
                    "appsecret": self.settings.app_secret, "tr_id": "FHKST03010230", "custtype": "P",
                },
                params=params,
            )
            _ensure_kis_http_success(response, "minute chart")
            payload = response.json()
        except KisApiError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise KisApiError("KIS minute chart request failed") from exc

        if payload.get("rt_cd") != "0":
            code = str(payload.get("msg_cd", "unknown"))[:32]
            raise KisApiError(f"KIS rejected the minute chart request (code {code})")
        rows = payload.get("output2")
        if not isinstance(rows, list):
            raise KisApiError("KIS minute chart response did not contain an output array")
        bars: list[MinuteBar] = []
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("invalid row")
                row_date = str(row.get("stck_bsop_date", ""))
                row_time = str(row.get("stck_cntg_hour", ""))
                if row_date != session_date.strftime("%Y%m%d") or len(row_time) != 6 or not row_time.isdigit():
                    raise ValueError("invalid bar timestamp")
                timestamp = datetime.combine(session_date, datetime_time(
                    int(row_time[:2]), int(row_time[2:4]), int(row_time[4:])
                ), tzinfo=ZoneInfo("Asia/Seoul"))
                volume = _volume(row.get("cntg_vol"))
                if volume is None:
                    raise ValueError("missing bar volume")
                bar = MinuteBar(
                    timestamp=timestamp, open=_decimal(row.get("stck_oprc"), "open") or Decimal(0),
                    high=_decimal(row.get("stck_hgpr"), "high") or Decimal(0),
                    low=_decimal(row.get("stck_lwpr"), "low") or Decimal(0),
                    close=_decimal(row.get("stck_prpr"), "close") or Decimal(0),
                    volume=volume,
                    spread_bps=None,
                )
                bars.append(bar)
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise KisApiError("KIS minute chart response contained invalid bar data") from exc
        bars.sort(key=lambda bar: bar.timestamp)
        if len(bars) > 120 or len({bar.timestamp for bar in bars}) != len(bars):
            raise KisApiError("KIS minute chart response exceeded the supported page contract")
        return bars

    def get_domestic_minute_session_bars(
        self, symbol: str, session_date: date, end_time: str = "153000", *, max_pages: int = 6,
    ) -> list["MinuteBar"]:
        """Walk bounded KIS time windows backward and return one complete KRX session prefix.

        Each request returns at most 120 bars. The next request ends one minute before
        the earliest returned bar; overlapping bars are accepted only when identical.
        """
        from .quant import MinuteBar

        if not 1 <= max_pages <= 6:
            raise ValueError("max_pages must be between 1 and 6")
        # Reuse the single-page method's symbol/time validation before issuing requests.
        if not (symbol.strip().isdigit() and len(symbol.strip()) == 6):
            raise ValueError("KR symbols must be six digits")
        if (len(end_time) != 6 or not end_time.isdigit() or int(end_time[:2]) > 23
                or int(end_time[2:4]) > 59 or int(end_time[4:]) > 59):
            raise ValueError("end_time must use HHMMSS")

        session_open = datetime_time(9, 0)
        bars_by_timestamp: dict[datetime, MinuteBar] = {}
        cursor = end_time
        for page_number in range(max_pages):
            page = self.get_domestic_minute_bars(symbol, session_date, cursor)
            for bar in page:
                if bar.timestamp.date() != session_date or not (session_open <= bar.timestamp.time()):
                    continue
                existing = bars_by_timestamp.get(bar.timestamp)
                if existing is not None and existing != bar:
                    raise KisApiError("KIS minute chart pages returned conflicting duplicate bars")
                bars_by_timestamp[bar.timestamp] = bar

            if not page or page[0].timestamp.time() <= session_open:
                break
            if page_number + 1 == max_pages:
                raise KisApiError("KIS minute chart session exceeded the supported pagination limit")
            next_cursor = (page[0].timestamp - timedelta(minutes=1)).strftime("%H%M%S")
            if next_cursor >= cursor or next_cursor < "090000":
                if next_cursor < "090000":
                    break
                raise KisApiError("KIS minute chart pagination did not move backward")
            cursor = next_cursor

        return [bars_by_timestamp[timestamp] for timestamp in sorted(bars_by_timestamp)]

    def get_overseas_minute_bars(
        self, symbol: str, exchange: Exchange, session_date: date | None = None,
    ) -> list["MinuteBar"]:
        """Fetch one bounded page of current US minute bars from KIS.

        KIS overseas minute chart is cursor based rather than date-addressable. A
        requested session date filters this latest page; it does not trigger an
        unbounded historical pagination walk.
        """
        from .quant import MinuteBar

        if self.settings.environment != "real":
            raise KisApiError("KIS overseas minute chart is not available in the paper environment")
        normalized_symbol = symbol.strip().upper()
        if not (1 <= len(normalized_symbol) <= 16 and all(ch.isalnum() or ch in ".-" for ch in normalized_symbol)):
            raise ValueError("US symbols may contain only letters, digits, dot, or hyphen")
        if exchange not in {"NAS", "NYS", "AMS"}:
            raise ValueError("exchange must be NAS, NYS, or AMS")
        exchange_code = {"NAS": "NAS", "NYS": "NYS", "AMS": "AMS"}[exchange]
        params = {
            "AUTH": "", "EXCD": exchange_code, "SYMB": normalized_symbol,
            "NMIN": "1", "PINC": "0", "NEXT": "", "NREC": "120", "FILL": "", "KEYB": "",
        }
        try:
            response = self._get(
                f"{self.settings.base_url}{OVERSEAS_MINUTE_CHART_PATH}",
                headers={
                    "authorization": f"Bearer {self._access_token()}", "appkey": self.settings.app_key,
                    "appsecret": self.settings.app_secret, "tr_id": "HHDFS76950200", "custtype": "P",
                },
                params=params,
            )
            _ensure_kis_http_success(response, "overseas minute chart")
            payload = response.json()
        except KisApiError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise KisApiError("KIS overseas minute chart request failed") from exc

        if payload.get("rt_cd") != "0":
            code = str(payload.get("msg_cd", "unknown"))[:32]
            raise KisApiError(f"KIS rejected the overseas minute chart request (code {code})")
        rows = payload.get("output2")
        if not isinstance(rows, list):
            raise KisApiError("KIS overseas minute chart response did not contain an output array")
        bars: list[MinuteBar] = []
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("invalid row")
                row_date = str(row.get("xymd", ""))
                row_time = str(row.get("xhms", ""))
                if len(row_date) != 8 or not row_date.isdigit() or len(row_time) != 6 or not row_time.isdigit():
                    raise ValueError("invalid bar timestamp")
                bar_date = datetime.strptime(row_date, "%Y%m%d").date()
                if session_date is not None and bar_date != session_date:
                    continue
                timestamp = datetime.combine(bar_date, datetime_time(
                    int(row_time[:2]), int(row_time[2:4]), int(row_time[4:]),
                ), tzinfo=ZoneInfo("America/New_York"))
                volume = _volume(row.get("evol", row.get("tvol")))
                if volume is None:
                    raise ValueError("missing bar volume")
                bars.append(MinuteBar(
                    timestamp=timestamp,
                    open=_decimal(row.get("open"), "open") or Decimal(0),
                    high=_decimal(row.get("high"), "high") or Decimal(0),
                    low=_decimal(row.get("low"), "low") or Decimal(0),
                    close=_decimal(row.get("last"), "close") or Decimal(0),
                    volume=volume, spread_bps=None,
                ))
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise KisApiError("KIS overseas minute chart response contained invalid bar data") from exc
        bars.sort(key=lambda bar: bar.timestamp)
        if len(bars) > 120 or len({bar.timestamp for bar in bars}) != len(bars):
            raise KisApiError("KIS overseas minute chart response exceeded the supported page contract")
        return bars

    def get_account_balance(self, market: Market, *, overseas_exchange: str = "NASD") -> AccountBalance:
        """Read balances/positions. Results intentionally omit account identifiers."""
        account = self.settings.account_number
        if len(account) != 10 or not account.isdigit():
            raise ValueError("KIS account number must contain 10 digits (8 + 2)")
        cano, product = account[:8], account[8:]
        if market == "KR":
            path = "/uapi/domestic-stock/v1/trading/inquire-balance"
            tr_id = "VTTC8434R" if self.settings.environment == "paper" else "TTTC8434R"
            params = {
                "CANO": cano, "ACNT_PRDT_CD": product, "AFHR_FLPR_YN": "N", "OFL_YN": "",
                "INQR_DVSN": "01", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            }
            currency = "KRW"
            positions_field, summary_field = "output1", "output2"
            fk_field, nk_field = "CTX_AREA_FK100", "CTX_AREA_NK100"
        elif market == "US":
            if overseas_exchange not in {"NASD", "NYSE", "AMEX"}:
                raise ValueError("overseas_exchange must be NASD, NYSE, or AMEX")
            path = "/uapi/overseas-stock/v1/trading/inquire-balance"
            tr_id = "VTTS3012R" if self.settings.environment == "paper" else "TTTS3012R"
            params = {
                "CANO": cano, "ACNT_PRDT_CD": product, "OVRS_EXCG_CD": overseas_exchange,
                "TR_CRCY_CD": "USD", "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
            }
            currency = "USD"
            positions_field, summary_field = "output1", "output2"
            fk_field, nk_field = "CTX_AREA_FK200", "CTX_AREA_NK200"
        else:
            raise ValueError("market must be KR or US")

        positions: list[dict[str, Any]] = []
        summary: Any = {}
        continuation = ""
        for page_number in range(20):
            request_headers = {
                "authorization": f"Bearer {self._access_token()}", "appkey": self.settings.app_key,
                "appsecret": self.settings.app_secret, "tr_id": tr_id, "custtype": "P",
            }
            if continuation:
                request_headers["tr_cont"] = continuation
            try:
                response = self._get(f"{self.settings.base_url}{path}", headers=request_headers, params=params)
                _ensure_kis_http_success(response, "account balance")
                payload = response.json()
            except KisApiError:
                raise
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise KisApiError("KIS account balance request failed") from exc

            if payload.get("rt_cd") != "0":
                code = str(payload.get("msg_cd", "unknown"))[:32]
                raise KisApiError(f"KIS rejected the account request (code {code})")
            if page_number == 0:
                summary = payload.get(summary_field)
            page_rows = payload.get(positions_field)
            if isinstance(page_rows, list):
                positions.extend(row for row in page_rows if isinstance(row, dict))
            elif isinstance(page_rows, dict):
                positions.append(page_rows)

            response_continuation = response.headers.get("tr_cont", "").upper()
            if response_continuation not in {"M", "F"}:
                break
            if page_number == 19:
                raise KisApiError("KIS account balance exceeded the supported pagination limit")
            params[fk_field] = str(payload.get(fk_field.lower(), ""))
            params[nk_field] = str(payload.get(nk_field.lower(), ""))
            continuation = "N"
        else:
            raise KisApiError("KIS account balance pagination did not complete")

        rows = positions
        if isinstance(summary, list):
            summary = summary[0] if summary else {}
        if not isinstance(summary, dict) or not isinstance(rows, list):
            raise KisApiError("KIS account response was missing balance fields")
        # Return only explicitly allowed business fields; never expose account numbers or raw payloads.
        summary_keys = ("dnca_tot_amt", "tot_evlu_amt", "nass_amt", "tot_pftrt", "evlu_pfls_smtl_amt")
        position_keys = ("pdno", "prdt_name", "hldg_qty", "ord_psbl_qty", "pchs_avg_pric", "prpr", "evlu_amt", "evlu_pfls_amt", "evlu_pfls_rt", "ovrs_pdno", "ovrs_item_name", "ovrs_cblc_qty", "ord_psbl_qty", "avg_unpr3", "now_pric2", "ovrs_stck_evlu_amt", "frcr_evlu_pfls_amt")
        return AccountBalance(
            environment=self.settings.environment,
            market=market,
            currency=currency,
            summary={key: value for key, value in summary.items() if key in summary_keys},
            positions=[{key: value for key, value in row.items() if key in position_keys} for row in rows if isinstance(row, dict)],
            retrieved_at=datetime.now(timezone.utc),
        )

    def close(self) -> None:
        self._http.close()
