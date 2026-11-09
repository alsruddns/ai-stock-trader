"""Read-only KIS REST quote and account client. No order APIs are implemented."""

from __future__ import annotations

import logging
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
DOMESTIC_VOLUME_RANK_PATH = "/uapi/domestic-stock/v1/quotations/volume-rank"
OVERSEAS_TRADE_VALUE_RANK_PATH = "/uapi/overseas-stock/v1/ranking/trade-pbmn"
KIS_MINUTE_CHART_PAGE_SIZE = 120
MAX_OVERSEAS_MINUTE_SESSION_PAGES = 20
READ_ONLY_GET_RETRY_LIMIT = 1
READ_ONLY_GET_RETRY_DELAY_SECONDS = 0.5
_RETRYABLE_READ_HTTP_STATUSES = {500, 502, 503, 504}
_RETRYABLE_BROKER_CODES = {"EGW00001", "EGW00002"}
_RATE_LIMIT_BROKER_CODES = {"EGW00201"}
_RESPONSE_JSON_CACHE_KEY = "kis_read_only_json_payload"
_RESPONSE_JSON_CACHE_MISSING = object()
_logger = logging.getLogger(__name__)


class KisApiError(RuntimeError):
    """Safe, non-sensitive error intended for application logging/handling."""

    def __init__(self, message: str, *, http_status: int | None = None,
                 broker_code: str | None = None, response_content_type: str | None = None,
                 response_bytes: int | None = None, response_fields: tuple[str, ...] = (),
                 response_error_category: str | None = None, response_error_code: str | None = None,
                 transport_error_category: str | None = None):
        super().__init__(message)
        self.http_status = http_status
        self.broker_code = broker_code
        self.response_content_type = response_content_type
        self.response_bytes = response_bytes
        self.response_fields = response_fields
        self.response_error_category = response_error_category
        self.response_error_code = response_error_code
        self.transport_error_category = transport_error_category


class KisSharedRateLimitUnavailable(KisApiError):
    """KIS requests are blocked because PostgreSQL cannot reserve a shared slot."""


class KisRateLimitError(KisApiError):
    """KIS REST request quota was reached; callers should wait before retrying."""


def _safe_transport_error_category(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connect"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "protocol"
    if isinstance(exc, httpx.NetworkError):
        return "network"
    return "other"


def _safe_kis_request_failure(operation: str, exc: Exception) -> KisApiError:
    category = (_safe_transport_error_category(exc) if isinstance(exc, httpx.HTTPError)
                else "invalid_response")
    return KisApiError(
        f"KIS {operation} request failed (transport category: {category}).",
        transport_error_category=category,
    )


def _safe_broker_code(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    candidate = value.strip()
    if not re.fullmatch(r"[A-Za-z]{3,4}\d{4,5}", candidate):
        return "unknown"
    return candidate.upper()


def _safe_response_field_names(payload: object) -> tuple[str, ...]:
    """Return bounded JSON key names without exposing any response values."""
    if not isinstance(payload, dict):
        return ()
    return tuple(
        key for key in payload
        if isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", key)
    )[:16]


def _response_json(response: httpx.Response) -> Any:
    cached = response.extensions.get(_RESPONSE_JSON_CACHE_KEY, _RESPONSE_JSON_CACHE_MISSING)
    if cached is not _RESPONSE_JSON_CACHE_MISSING:
        return cached
    payload = response.json()
    response.extensions[_RESPONSE_JSON_CACHE_KEY] = payload
    return payload


def _ensure_kis_http_success(response: httpx.Response, operation: str) -> None:
    if response.status_code < 400:
        return
    code = "unknown"
    try:
        payload = _response_json(response)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        code = _safe_broker_code(payload.get("msg_cd"))
        response_fields = _safe_response_field_names(payload)
    else:
        response_fields = ()
    error_value = next((payload.get(key) for key in ("error_code", "error")
                        if isinstance(payload, dict) and isinstance(payload.get(key), str)), None)
    normalized_error = error_value.strip().lower().replace("-", "_") if error_value else ""
    raw_error_code = payload.get("error_code") if isinstance(payload, dict) else None
    response_error_code = (raw_error_code.strip() if isinstance(raw_error_code, str)
                           and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", raw_error_code.strip()) else None)
    allowed_errors = {
        "access_denied", "forbidden", "invalid_client", "invalid_grant", "invalid_request",
        "server_error", "temporarily_unavailable", "unauthorized_client", "unsupported_grant_type",
    }
    broker_error_categories = {
        "EGW00001": "broker_temporary_error",
        "EGW00002": "broker_server_error",
    }
    response_error_category = (normalized_error if normalized_error in allowed_errors
                               else broker_error_categories.get(response_error_code))
    if response.status_code == 429 or code == "EGW00201":
        raise KisRateLimitError(
            "KIS rate limit reached; wait before requesting again.",
            http_status=response.status_code,
            broker_code=code,
            response_content_type=_safe_content_type(response.headers.get("content-type")),
            response_bytes=len(response.content),
            response_fields=response_fields,
            response_error_category=response_error_category,
            response_error_code=response_error_code,
        )
    raise KisApiError(
        f"KIS {operation} request failed (HTTP {response.status_code}, code {code}).",
        http_status=response.status_code,
        broker_code=code,
        response_content_type=_safe_content_type(response.headers.get("content-type")),
        response_bytes=len(response.content),
        response_fields=response_fields,
        response_error_category=response_error_category,
        response_error_code=response_error_code,
    )


def _safe_content_type(value: str | None) -> str | None:
    if not value:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type if re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", media_type) else None


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
        enabled_variable = "KIS_REAL_MARKET_DATA_ENABLED" if mode == "real" else "KIS_MARKET_DATA_ENABLED"
        return cls(
            enabled=os.getenv(enabled_variable, "false").strip().lower() == "true",
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
    best_ask: Decimal | None = None
    best_bid: Decimal | None = None
    spread_bps: Decimal | None = None


@dataclass(frozen=True)
class MarketCandidate:
    """Read-only rank result enriched with quote/liquidity checks; never an order signal."""

    market: Market
    symbol: str
    name: str
    exchange: str | None
    rank: int
    currency: str
    price: Decimal
    volume: int
    turnover: Decimal
    best_ask: Decimal | None
    best_bid: Decimal | None
    spread_bps: Decimal | None
    passes_market_filter: bool
    filter_reason: str
    freshness_verified: bool = False
    executable: bool = False
    source: str = "KIS_REST_RANKING"
    retrieved_at: datetime = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class AccountBalance:
    environment: Literal["paper", "real"]
    market: Market
    currency: str
    summary: dict[str, Any]
    positions: list[dict[str, Any]]
    retrieved_at: datetime
    source: str = "KIS_REST_ACCOUNT_SNAPSHOT"


@dataclass(frozen=True)
class HistoricalMinuteSession:
    """A bounded historical read plus non-price pagination diagnostics."""

    bars: list["MinuteBar"]
    pages_fetched: int
    source_record_count: int
    first_source_timestamp: datetime | None
    last_source_timestamp: datetime | None
    continuation_states: list[str]


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
        for attempt in range(READ_ONLY_GET_RETRY_LIMIT + 1):
            self._wait_for_rest_slot()
            try:
                response = self._http.get(url, headers=headers, params=params)
            except httpx.HTTPError as exc:
                category = _safe_transport_error_category(exc)
                if attempt >= READ_ONLY_GET_RETRY_LIMIT or category not in {
                    "timeout", "connect", "protocol", "network",
                }:
                    raise
                _logger.warning(
                    "KIS read-only GET transport failure; retrying attempt %d/%d (category=%s).",
                    attempt + 1, READ_ONLY_GET_RETRY_LIMIT + 1, category,
                )
                time.sleep(READ_ONLY_GET_RETRY_DELAY_SECONDS)
                continue

            code = "unknown"
            try:
                payload = _response_json(response)
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict):
                code = _safe_broker_code(payload.get("msg_cd"))
            retryable = (
                response.status_code not in {429}
                and code not in _RATE_LIMIT_BROKER_CODES
                and (
                    response.status_code in _RETRYABLE_READ_HTTP_STATUSES
                    or code in _RETRYABLE_BROKER_CODES
                )
            )
            if not retryable or attempt >= READ_ONLY_GET_RETRY_LIMIT:
                return response

            _logger.warning(
                "KIS read-only GET returned a transient response; retrying attempt %d/%d "
                "(HTTP %d, broker code %s).",
                attempt + 1, READ_ONLY_GET_RETRY_LIMIT + 1, response.status_code, code,
            )
            response.close()
            time.sleep(READ_ONLY_GET_RETRY_DELAY_SECONDS)
        raise KisApiError("KIS read-only GET retry loop ended unexpectedly")

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
                payload = _response_json(response)
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
                raise _safe_kis_request_failure("authentication", exc) from exc

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
            payload = _response_json(response)
        except KisApiError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise _safe_kis_request_failure("quote", exc) from exc

        if not isinstance(payload, dict):
            raise KisApiError("KIS quote response was not a JSON object")
        if payload.get("rt_cd") != "0":
            code = _safe_broker_code(payload.get("msg_cd"))
            fields = _safe_response_field_names(payload)
            response_fields = ",".join(fields) if fields else "none"
            content_type = _safe_content_type(response.headers.get("content-type")) or "unknown"
            raise KisApiError(
                "KIS rejected the quote request "
                f"(HTTP {response.status_code}, code {code}, content-type {content_type}, "
                f"response-bytes {len(response.content)}, fields {response_fields})",
                http_status=response.status_code,
                broker_code=code,
                response_content_type=content_type if content_type != "unknown" else None,
                response_bytes=len(response.content),
                response_fields=fields,
            )
        output = payload.get("output")
        if isinstance(output, list):
            if len(output) != 1 or not isinstance(output[0], dict):
                raise KisApiError("KIS quote response did not contain exactly one output object")
            output = output[0]
        if not isinstance(output, dict):
            raise KisApiError("KIS quote response did not contain an output object")

        if market == "KR":
            price = _decimal(output.get("stck_prpr"), "price")
            currency = "KRW"
            change = _decimal(output.get("prdy_vrss"), "change")
            change_percent = _decimal(output.get("prdy_ctrt"), "change percent")
            volume = _volume(output.get("acml_vol"))
            best_ask = _decimal(output.get("askp1"), "best ask")
            best_bid = _decimal(output.get("bidp1"), "best bid")
        else:
            price = _decimal(output.get("last"), "price")
            currency = "USD"
            change = _decimal(output.get("diff"), "change")
            change_percent = _decimal(output.get("rate"), "change percent")
            volume = _volume(output.get("tvol"))
            best_ask = _decimal(output.get("pask", output.get("askp")), "best ask")
            best_bid = _decimal(output.get("pbid", output.get("bidp")), "best bid")
        if price is None or price <= 0:
            raise KisApiError("KIS quote response did not contain a valid price")
        spread_bps = self._spread_bps(best_ask, best_bid)
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
            best_ask=best_ask,
            best_bid=best_bid,
            spread_bps=spread_bps,
        )

    @staticmethod
    def _spread_bps(best_ask: Decimal | None, best_bid: Decimal | None) -> Decimal | None:
        if best_ask is None or best_bid is None or best_ask <= 0 or best_bid <= 0:
            return None
        if best_ask < best_bid:
            raise KisApiError("KIS quote returned a crossed bid/ask")
        midpoint = (best_ask + best_bid) / Decimal(2)
        if midpoint <= 0:
            return None
        return (best_ask - best_bid) / midpoint * Decimal(10_000)

    def get_ranked_candidates(
        self,
        market: Market,
        *,
        exchange: Exchange | None = None,
        limit: int = 10,
        minimum_turnover: Decimal = Decimal(0),
        maximum_spread_bps: Decimal = Decimal(15),
    ) -> list[MarketCandidate]:
        """Read a bounded KIS liquidity ranking and fail closed on missing spread data.

        This ranking is discovery data only. It never subscribes symbols, creates a
        strategy signal, writes an order, or claims that a candidate is executable.
        """
        if not 1 <= limit <= 20:
            raise ValueError("candidate limit must be between 1 and 20")
        if minimum_turnover < 0 or maximum_spread_bps < 0:
            raise ValueError("candidate screening thresholds must be non-negative")
        if market == "KR":
            if exchange is not None:
                raise ValueError("exchange is only used for US candidates")
            path = DOMESTIC_VOLUME_RANK_PATH
            tr_id = "FHPST01710000"
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "1",
                "FID_BLNG_CLS_CODE": "3",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "0",
                "FID_INPUT_PRICE_2": "0",
                "FID_VOL_CNT": "0",
                "FID_INPUT_DATE_1": "",
            }
        elif market == "US":
            if exchange is None:
                raise ValueError("US candidates require exchange NAS, NYS, or AMS")
            path = OVERSEAS_TRADE_VALUE_RANK_PATH
            tr_id = "HHDFS76320010"
            params = {"EXCD": exchange, "NDAY": "0", "VOL_RANG": "0", "AUTH": "",
                      "KEYB": "", "PRC1": "", "PRC2": ""}
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
            _ensure_kis_http_success(response, "candidate ranking")
            payload = _response_json(response)
        except KisApiError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise _safe_kis_request_failure("candidate ranking", exc) from exc
        if payload.get("rt_cd") != "0":
            code = _safe_broker_code(payload.get("msg_cd"))
            raise KisApiError(f"KIS rejected the candidate ranking request (code {code})", broker_code=code)

        rows = payload.get("output") if market == "KR" else payload.get("output2")
        if not isinstance(rows, list):
            raise KisApiError("KIS candidate ranking response did not contain a result array")

        candidates: list[MarketCandidate] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                raise KisApiError("KIS candidate ranking response contained an invalid row")
            if market == "KR":
                symbol = str(row.get("mksc_shrn_iscd", "")).strip()
                if not (symbol.isdigit() and len(symbol) == 6):
                    continue
                # The ranking endpoint has no quote spread fields; obtain a separate
                # current snapshot before a symbol can pass the market filter.
                quote = self.get_quote("KR", symbol)
                name = str(row.get("hts_kor_isnm", "")).strip()
                rank = _volume(row.get("data_rank")) or (len(candidates) + 1)
                turnover = _decimal(row.get("acml_tr_pbmn"), "turnover") or Decimal(0)
                volume = _volume(row.get("acml_vol")) or 0
                price = quote.price
                best_ask, best_bid, spread_bps = quote.best_ask, quote.best_bid, quote.spread_bps
                currency = "KRW"
            else:
                symbol = str(row.get("symb", "")).strip().upper()
                if not symbol or len(symbol) > 16:
                    continue
                name = str(row.get("name", "")).strip()
                rank = _volume(row.get("rank")) or (len(candidates) + 1)
                price = _decimal(row.get("last"), "price") or Decimal(0)
                volume = _volume(row.get("tvol")) or 0
                turnover = _decimal(row.get("tamt"), "turnover") or Decimal(0)
                best_ask = _decimal(row.get("pask"), "best ask")
                best_bid = _decimal(row.get("pbid"), "best bid")
                spread_bps = self._spread_bps(best_ask, best_bid)
                currency = "USD"

            if price <= 0 or volume <= 0 or turnover < 0:
                raise KisApiError("KIS candidate ranking response contained invalid market values")
            if spread_bps is None:
                passes = False
                reason = "LIVE_SPREAD_UNAVAILABLE"
            elif turnover < minimum_turnover:
                passes = False
                reason = "TURNOVER_BELOW_MINIMUM"
            elif spread_bps > maximum_spread_bps:
                passes = False
                reason = "SPREAD_ABOVE_MAXIMUM"
            else:
                passes = True
                reason = "MARKET_FILTERS_PASSED"
            candidates.append(MarketCandidate(
                market=market, symbol=symbol, name=name,
                exchange=exchange if market == "US" else None, rank=rank, currency=currency,
                price=price, volume=volume, turnover=turnover,
                best_ask=best_ask, best_bid=best_bid, spread_bps=spread_bps,
                passes_market_filter=passes, filter_reason=reason,
                retrieved_at=datetime.now(timezone.utc),
            ))
        return candidates

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
            # The official historical-minute example uses Y to return past-date bars.
            "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "",
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
            payload = _response_json(response)
        except KisApiError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise _safe_kis_request_failure("minute chart", exc) from exc

        if payload.get("rt_cd") != "0":
            code = _safe_broker_code(payload.get("msg_cd"))
            raise KisApiError(f"KIS rejected the minute chart request (code {code})", broker_code=code)
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
                if len(row_date) != 8 or not row_date.isdigit() or len(row_time) != 6 or not row_time.isdigit():
                    raise ValueError("invalid bar timestamp")
                parsed_date = datetime.strptime(row_date, "%Y%m%d").date()
                if parsed_date != session_date:
                    continue
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
        if len(bars) > KIS_MINUTE_CHART_PAGE_SIZE or len({bar.timestamp for bar in bars}) != len(bars):
            raise KisApiError("KIS minute chart response exceeded the supported page contract")
        return bars

    def get_domestic_minute_session_bars(
        self, symbol: str, session_date: date, end_time: str = "153000", *, max_pages: int = 6,
    ) -> list["MinuteBar"]:
        """Fetch a bounded historical KRX session and return only its bars."""
        return self.get_domestic_minute_session_snapshot(
            symbol, session_date, end_time, max_pages=max_pages,
        ).bars

    def get_domestic_minute_session_snapshot(
        self, symbol: str, session_date: date, end_time: str = "153000", *, max_pages: int = 6,
    ) -> HistoricalMinuteSession:
        """Walk bounded KIS time windows backward and return one complete KRX session prefix.

        Each request returns at most 120 bars. The next request ends one minute before
        the earliest returned bar; overlapping bars are accepted only when identical.
        Preserve source page counts and timestamps so callers can audit completeness.
        """
        from .quant import MinuteBar
        from .market_calendar import exchange_continuous_session_window

        if not 1 <= max_pages <= 6:
            raise ValueError("max_pages must be between 1 and 6")
        # Reuse the single-page method's symbol/time validation before issuing requests.
        if not (symbol.strip().isdigit() and len(symbol.strip()) == 6):
            raise ValueError("KR symbols must be six digits")
        if (len(end_time) != 6 or not end_time.isdigit() or int(end_time[:2]) > 23
                or int(end_time[2:4]) > 59 or int(end_time[4:]) > 59):
            raise ValueError("end_time must use HHMMSS")

        session_window = exchange_continuous_session_window(
            "KR", datetime.combine(session_date, datetime_time(12), tzinfo=ZoneInfo("Asia/Seoul")),
        )
        if session_window is None:
            return HistoricalMinuteSession([], 0, 0, None, None, [])
        session_open, session_close = session_window
        bars_by_timestamp: dict[datetime, MinuteBar] = {}
        source_timestamps: list[datetime] = []
        source_record_count = 0
        pages_fetched = 0
        cursor = end_time
        for page_number in range(max_pages):
            page = self.get_domestic_minute_bars(symbol, session_date, cursor)
            pages_fetched += 1
            source_record_count += len(page)
            source_timestamps.extend(bar.timestamp for bar in page)
            for bar in page:
                timestamp_utc = bar.timestamp.astimezone(timezone.utc)
                if not (session_open <= timestamp_utc < session_close):
                    continue
                existing = bars_by_timestamp.get(bar.timestamp)
                if existing is not None and existing != bar:
                    raise KisApiError("KIS minute chart pages returned conflicting duplicate bars")
                bars_by_timestamp[bar.timestamp] = bar

            if not page or page[0].timestamp.astimezone(timezone.utc) <= session_open:
                break
            if page_number + 1 == max_pages:
                raise KisApiError("KIS minute chart session exceeded the supported pagination limit")
            next_cursor = (page[0].timestamp - timedelta(minutes=1)).strftime("%H%M%S")
            if next_cursor >= cursor or next_cursor < "090000":
                if next_cursor < "090000":
                    break
                raise KisApiError("KIS minute chart pagination did not move backward")
            cursor = next_cursor

        return HistoricalMinuteSession(
            bars=[bars_by_timestamp[timestamp] for timestamp in sorted(bars_by_timestamp)],
            pages_fetched=pages_fetched,
            source_record_count=source_record_count,
            first_source_timestamp=min(source_timestamps) if source_timestamps else None,
            last_source_timestamp=max(source_timestamps) if source_timestamps else None,
            # This endpoint paginates with an explicit time parameter, not KIS tr_cont.
            continuation_states=[],
        )

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
        bars, _continuation = self._get_overseas_minute_page(
            normalized_symbol, exchange_code, pinc="0", next_page="", keyb="", continuation="",
        )
        return [bar for bar in bars if session_date is None or bar.timestamp.date() == session_date]

    def get_overseas_minute_session_bars(
        self, symbol: str, exchange: Exchange, session_date: date, *,
        max_pages: int = MAX_OVERSEAS_MINUTE_SESSION_PAGES,
    ) -> list["MinuteBar"]:
        """Fetch a bounded historical US session using KIS previous-day pagination."""
        return self.get_overseas_minute_session_snapshot(
            symbol, exchange, session_date, max_pages=max_pages,
        ).bars

    def get_overseas_minute_session_snapshot(
        self, symbol: str, exchange: Exchange, session_date: date, *,
        max_pages: int = MAX_OVERSEAS_MINUTE_SESSION_PAGES,
    ) -> HistoricalMinuteSession:
        """Return requested bars with safe page-count and source-time diagnostics."""
        from .quant import MinuteBar

        if self.settings.environment != "real":
            raise KisApiError("KIS overseas minute chart is not available in the paper environment")
        normalized_symbol = symbol.strip().upper()
        if not (1 <= len(normalized_symbol) <= 16 and all(ch.isalnum() or ch in ".-" for ch in normalized_symbol)):
            raise ValueError("US symbols may contain only letters, digits, dot, or hyphen")
        if exchange not in {"NAS", "NYS", "AMS"}:
            raise ValueError("exchange must be NAS, NYS, or AMS")
        if not 1 <= max_pages <= MAX_OVERSEAS_MINUTE_SESSION_PAGES:
            raise ValueError(f"max_pages must be between 1 and {MAX_OVERSEAS_MINUTE_SESSION_PAGES}")
        exchange_code = {"NAS": "NAS", "NYS": "NYS", "AMS": "AMS"}[exchange]
        from .market_calendar import exchange_continuous_session_window

        session_window = exchange_continuous_session_window(
            "US", datetime.combine(session_date, datetime_time(hour=12), tzinfo=ZoneInfo("America/New_York")),
        )
        if session_window is None:
            return HistoricalMinuteSession([], 0, 0, None, None, [])
        session_open, session_close = (instant.astimezone(timezone.utc) for instant in session_window)

        bars_by_time: dict[datetime, MinuteBar] = {}
        source_timestamps: list[datetime] = []
        source_record_count = 0
        pages_fetched = 0
        continuation_states: list[str] = []
        continuation = ""
        next_page = ""
        keyb = ""
        previous_oldest: datetime | None = None
        for page_number in range(max_pages):
            page, response_continuation = self._get_overseas_minute_page(
                normalized_symbol, exchange_code, pinc="1", next_page=next_page,
                keyb=keyb, continuation=continuation,
            )
            pages_fetched += 1
            source_record_count += len(page)
            source_timestamps.extend(bar.timestamp for bar in page)
            continuation_states.append(response_continuation or "EMPTY")
            for bar in page:
                bar_at = bar.timestamp.astimezone(timezone.utc)
                if bar.timestamp.date() != session_date or not session_open <= bar_at < session_close:
                    continue
                prior = bars_by_time.get(bar.timestamp)
                if prior is not None and prior != bar:
                    raise KisApiError("KIS overseas minute pages returned conflicting duplicate bars")
                bars_by_time[bar.timestamp] = bar

            oldest = page[0].timestamp if page else None
            if oldest is not None and oldest.date() < session_date:
                break
            if response_continuation not in {"", "D", "E", "F", "M", "N"}:
                raise KisApiError("KIS overseas minute response returned an unsupported continuation state")
            # KIS may omit the continuation header on a full page. In that case
            # the documented NEXT/KEYB cursor still allows a bounded older-page read.
            if response_continuation not in {"M", "F"} and len(page) < KIS_MINUTE_CHART_PAGE_SIZE:
                break
            if page_number + 1 == max_pages:
                raise KisApiError("KIS overseas minute session exceeded the supported pagination limit")
            if oldest is None or (previous_oldest is not None and oldest >= previous_oldest):
                raise KisApiError("KIS overseas minute pagination did not move backward")
            previous_oldest = oldest
            keyb = (oldest - timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
            next_page = "1"
            continuation = "N"

        first_source_timestamp = min(source_timestamps) if source_timestamps else None
        last_source_timestamp = max(source_timestamps) if source_timestamps else None
        return HistoricalMinuteSession(
            bars=[bars_by_time[timestamp] for timestamp in sorted(bars_by_time)],
            pages_fetched=pages_fetched,
            source_record_count=source_record_count,
            first_source_timestamp=first_source_timestamp,
            last_source_timestamp=last_source_timestamp,
            continuation_states=continuation_states,
        )

    def _get_overseas_minute_page(
        self, symbol: str, exchange_code: str, *, pinc: str, next_page: str,
        keyb: str, continuation: str,
    ) -> tuple[list["MinuteBar"], str]:
        from .quant import MinuteBar

        params = {
            "AUTH": "", "EXCD": exchange_code, "SYMB": symbol,
            "NMIN": "1", "PINC": pinc, "NEXT": next_page,
            "NREC": str(KIS_MINUTE_CHART_PAGE_SIZE), "FILL": "", "KEYB": keyb,
        }
        request_headers = {
            "authorization": f"Bearer {self._access_token()}", "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret, "tr_id": "HHDFS76950200", "custtype": "P",
        }
        if continuation:
            request_headers["tr_cont"] = continuation
        try:
            response = self._get(
                f"{self.settings.base_url}{OVERSEAS_MINUTE_CHART_PATH}",
                headers=request_headers,
                params=params,
            )
            _ensure_kis_http_success(response, "overseas minute chart")
            payload = _response_json(response)
        except KisApiError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise _safe_kis_request_failure("overseas minute chart", exc) from exc

        if payload.get("rt_cd") != "0":
            code = _safe_broker_code(payload.get("msg_cd"))
            raise KisApiError(f"KIS rejected the overseas minute chart request (code {code})", broker_code=code)
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
        if len(bars) > KIS_MINUTE_CHART_PAGE_SIZE or len({bar.timestamp for bar in bars}) != len(bars):
            raise KisApiError("KIS overseas minute chart response exceeded the supported page contract")
        return bars, response.headers.get("tr_cont", "").strip().upper()

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
                payload = _response_json(response)
            except KisApiError:
                raise
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise _safe_kis_request_failure("account balance", exc) from exc

            if payload.get("rt_cd") != "0":
                code = _safe_broker_code(payload.get("msg_cd"))
                raise KisApiError(f"KIS rejected the account request (code {code})", broker_code=code)
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
