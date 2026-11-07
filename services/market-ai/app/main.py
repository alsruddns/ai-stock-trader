from enum import StrEnum
import os
import re
import threading
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Literal
import asyncio
import json
import hmac
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, WebSocket, WebSocketDisconnect
import psycopg
from pydantic import BaseModel, Field
from .kis import (AccountBalance, Exchange, HistoricalMinuteSession, KisApiError,
                  KisReadOnlyClient, KisSettings, KisRateLimitError,
                  KisSharedRateLimitUnavailable, Market, Quote)
from . import snapshot_store
from . import kis_stream
from .ai_pipeline import LocalModelRegistry
from .quant import MinuteBar, QuantConfig, backtest_orb_vwap
from .paper_ops import exchange_session_window, korean_calendar_compatibility
from .paper_automation import PaperAutomationReadiness
from .market_calendar import exchange_continuous_session_window, latest_closed_session_date
from .paper_runtime import PaperWatch, paper_session_monitor
from .session_quality import audit_minute_bar_session

_KIS_CLIENTS: dict[str, KisReadOnlyClient] = {}
_KIS_CLIENTS_LOCK = threading.RLock()


@asynccontextmanager
async def lifespan(_app):
    await paper_session_monitor.start()
    try:
        yield
    finally:
        try:
            await paper_session_monitor.stop()
        finally:
            close_kis_clients()

app = FastAPI(
    title="AI Stock Trader Market Analysis",
    version="0.1.0",
    description="Market analysis and opt-in local paper automation. It has no KIS order route.",
    lifespan=lifespan,
)


def get_kis_client() -> KisReadOnlyClient:
    return get_kis_market_data_client()


def get_kis_market_data_client(environment: Literal["paper", "real"] | None = None) -> KisReadOnlyClient:
    settings = KisSettings.from_environment(environment)
    if not settings.enabled:
        raise HTTPException(status_code=503, detail="KIS market data is disabled")
    if not settings.app_key or not settings.app_secret:
        raise HTTPException(status_code=503, detail="KIS market data credentials are not configured")
    return _get_or_create_kis_client(settings)


def get_kis_candidate_client(market: Market) -> KisReadOnlyClient:
    if market == "US":
        if os.getenv("KIS_REAL_MARKET_DATA_ENABLED", "false").strip().lower() != "true":
            raise HTTPException(status_code=503, detail="KIS REAL market-data screening is disabled")
        return get_kis_market_data_client("real")
    return get_kis_market_data_client()


def get_kis_real_market_data_client() -> KisReadOnlyClient:
    """Use the separately gated REAL read-only client for US market data."""
    return get_kis_market_data_client("real")


def get_market_data_client(market: Market = Path()) -> KisReadOnlyClient:
    """Route domestic REST data to PAPER and US REST data to the gated REAL environment."""
    return get_kis_real_market_data_client() if market == "US" else get_kis_client()


def get_kis_account_client(environment: Literal["paper", "real"]) -> KisReadOnlyClient:
    """Reuse one token-caching HTTP client per account environment."""
    settings = KisSettings.from_environment(environment)
    if (not settings.app_key or not settings.app_secret
            or not re.fullmatch(r"\d{10}", settings.account_number)):
        raise HTTPException(status_code=503, detail="KIS account credentials are not configured")
    return _get_or_create_kis_client(settings)


def _get_or_create_kis_client(settings: KisSettings) -> KisReadOnlyClient:
    environment = settings.environment
    with _KIS_CLIENTS_LOCK:
        current = _KIS_CLIENTS.get(environment)
        if current is not None and current.settings == settings:
            return current
        if current is not None:
            current.close()
        client = KisReadOnlyClient(settings)
        _KIS_CLIENTS[environment] = client
        return client


def close_kis_clients() -> None:
    with _KIS_CLIENTS_LOCK:
        clients = list(_KIS_CLIENTS.values())
        _KIS_CLIENTS.clear()
    for client in clients:
        client.close()


class Signal(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Bar(BaseModel):
    close: float = Field(gt=0)


class AnalysisRequest(BaseModel):
    market: str = Field(pattern="^(KR|US)$")
    symbol: str = Field(min_length=1, max_length=24)
    bars: list[Bar] = Field(min_length=2, max_length=500)


class QuantBarRequest(BaseModel):
    timestamp: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: int = Field(ge=0)
    spread_bps: Decimal | None = Field(default=None, ge=0)


class QuantBacktestRequest(BaseModel):
    market: Market
    symbol: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.-]+$")
    quantity: int = Field(default=1, gt=0, le=100_000)
    bars: list[QuantBarRequest] = Field(min_length=1, max_length=5000)


class QuantBacktestResponse(BaseModel):
    market: str
    symbol: str
    session: date
    trades: list[dict[str, object]]
    gross_pnl: Decimal
    costs: Decimal
    net_pnl: Decimal
    data_points: int
    signals: int
    executable: bool


class HistoricalMinuteBarsResponse(BaseModel):
    market: str
    symbol: str
    session_date: date | None
    source: str
    spread_bps_available: bool
    bars: list[QuantBarRequest]
    source_pages_fetched: int | None = None
    source_records_seen: int | None = None
    source_first_bar_at: datetime | None = None
    source_last_bar_at: datetime | None = None
    source_continuation_states: list[str] | None = None


class AiPredictionRequest(BaseModel):
    market: Market
    symbol: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.-]+$")
    bars: list[QuantBarRequest] = Field(min_length=21, max_length=500)


class ModelApprovalRequest(BaseModel):
    approved_by: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=20, max_length=1000)


class ModelChangeRequest(BaseModel):
    changed_by: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=20, max_length=1000)


class AnalysisResponse(BaseModel):
    market: str
    symbol: str
    signal: Signal
    confidence: float = Field(ge=0, le=1)
    source: str
    executable: bool
    reason: str


class QuoteResponse(BaseModel):
    market: Market
    symbol: str
    exchange: str | None
    currency: str
    price: Decimal
    change: Decimal | None
    change_percent: Decimal | None
    volume: int | None
    retrieved_at: datetime
    source: str
    best_ask: Decimal | None = None
    best_bid: Decimal | None = None
    spread_bps: Decimal | None = None


class MarketCandidateResponse(BaseModel):
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
    freshness_verified: bool
    executable: bool
    source: str
    retrieved_at: datetime


class CandidateScreenResponse(BaseModel):
    market: Market
    exchange: str | None
    currency: str
    minimum_turnover: Decimal
    maximum_spread_bps: Decimal
    candidates: list[MarketCandidateResponse]
    executable: bool = False


class CandidateSubscriptionResponse(BaseModel):
    market: Market
    exchange: str | None
    considered: int
    added: list[str]
    subscriptions: int
    executable: bool = False


class CandidateRefreshResponse(BaseModel):
    market: Market
    exchange: str | None
    considered: int
    added: list[str]
    removed: list[str]
    preserved: int
    open_orders: int
    subscriptions: int
    executable: bool = False


class AccountBalanceResponse(BaseModel):
    environment: str
    market: Market
    currency: str
    summary: dict[str, object]
    positions: list[dict[str, object]]
    retrieved_at: datetime
    source: str


def _session_quality_report(
    market: str, symbol: str, session_date: date, bars: list[MinuteBar], evaluated_at: datetime,
) -> dict[str, object]:
    try:
        return audit_minute_bar_session(market, symbol, session_date, bars, evaluated_at=evaluated_at)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Exchange calendar is unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Stored market data could not be validated") from exc


@app.get("/api/v1/health")
def health() -> dict[str, str | bool]:
    settings = KisSettings.from_environment()
    return {
        "status": "ok",
        "mode": "read-only-preview",
        "broker_connected": False,
        "kis_market_data_enabled": settings.enabled,
        "paper_session_monitor": paper_session_monitor.status()["state"],
        "kr_calendar_compatibility": korean_calendar_compatibility(),
        "paper_automation_enabled": PaperAutomationReadiness.from_environment().enabled,
        "live_trading_enabled": False,
    }


@app.get("/api/v1/paper/session")
def paper_session_status() -> dict[str, object]:
    """Expose the local paper feed monitor without secrets or order controls."""
    status = paper_session_monitor.status()
    status["kr_calendar_compatibility"] = korean_calendar_compatibility()
    return status


@app.get("/api/v1/market/sessions/{market}")
def market_session_status(market: Market = Path()) -> dict[str, object]:
    """Return exchange-calendar verified session times for local order preflight."""
    now = datetime.now(timezone.utc)
    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    try:
        window = exchange_session_window(market, now)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Market calendar could not verify this session")
    opened, closed = window if window else (None, None)
    return {
        "market": market,
        "session_date": now.astimezone(zone).date().isoformat(),
        "is_session": window is not None,
        "is_open": bool(window and opened <= now < closed),
        "open_at": opened.isoformat() if opened else None,
        "close_at": closed.isoformat() if closed else None,
        "evaluated_at": now.isoformat(),
    }


@app.get("/api/v1/market/sessions/{market}/latest-closed")
def latest_closed_market_session_status(market: Market = Path()) -> dict[str, object]:
    """Return the latest completed exchange session for cross-market read-only risk evidence."""
    now = datetime.now(timezone.utc)
    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    try:
        session_day = latest_closed_session_date(market, now)
        noon = datetime.combine(session_day, time(hour=12), tzinfo=zone)
        window = exchange_session_window(market, noon)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Latest closed market session could not be verified")
    if window is None:
        raise HTTPException(status_code=503, detail="Latest closed market session could not be verified")
    opened, closed = window
    return {
        "market": market,
        "session_date": session_day.isoformat(),
        "is_session": True,
        "is_open": False,
        "open_at": opened.isoformat(),
        "close_at": closed.isoformat(),
        "evaluated_at": now.isoformat(),
    }


@app.get("/api/v1/market/session-quality/{market}/{symbol}")
async def market_session_quality(
    market: Market = Path(),
    symbol: str = Path(pattern=r"^[A-Za-z0-9.-]{1,16}$"),
    session_date: date = Query(),
) -> dict[str, object]:
    """Read-only audit of stored continuous-session bars; never fills or changes data."""
    normalized_market = str(market).upper()
    normalized_symbol = symbol.strip().upper()
    if ((normalized_market == "KR" and not re.fullmatch(r"\d{6}", normalized_symbol))
            or (normalized_market == "US" and not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,15}", normalized_symbol))):
        raise HTTPException(status_code=422, detail="Symbol format does not match the selected market")
    evaluated_at = datetime.now(timezone.utc)
    report = _session_quality_report(normalized_market, normalized_symbol, session_date, [], evaluated_at)
    if report["status"] == "NON_SESSION":
        report["source_counts"] = {}
        return report
    if not snapshot_store.enabled():
        raise HTTPException(status_code=503, detail="Market snapshot persistence is disabled")
    try:
        bars = await asyncio.to_thread(
            snapshot_store.load_session_minute_bars, normalized_market, normalized_symbol, session_date,
        )
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Stored market data is unavailable") from exc
    report = _session_quality_report(normalized_market, normalized_symbol, session_date, bars, evaluated_at)
    try:
        source_counts = await asyncio.to_thread(
            snapshot_store.load_session_minute_bar_source_counts,
            normalized_market, [normalized_symbol], session_date,
        )
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Stored market-data provenance is unavailable") from exc
    report["source_counts"] = source_counts.get(normalized_symbol, {})
    return report


@app.get("/api/v1/market/session-quality/{market}")
async def market_session_quality_batch(
    market: Market = Path(),
    symbols: list[str] = Query(),
    session_date: date | None = Query(default=None),
    latest_closed: bool = Query(default=False),
) -> dict[str, object]:
    """Audit the watched symbols for one market with a single read-only DB query."""
    normalized_market = str(market).upper()
    if not 1 <= len(symbols) <= 50:
        raise HTTPException(status_code=422, detail="Provide 1 to 50 symbols")
    normalized_symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols))
    pattern = r"\d{6}" if normalized_market == "KR" else r"[A-Z][A-Z0-9.-]{0,15}"
    if any(not re.fullmatch(pattern, symbol) for symbol in normalized_symbols):
        raise HTTPException(status_code=422, detail="Symbol format does not match the selected market")
    evaluated_at = datetime.now(timezone.utc)
    if latest_closed:
        if session_date is not None:
            raise HTTPException(status_code=422, detail="Provide session_date or latest_closed, not both")
        try:
            session_date = latest_closed_session_date(normalized_market, evaluated_at)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="Latest closed exchange session is unavailable") from exc
    elif session_date is None:
        raise HTTPException(status_code=422, detail="Provide session_date or latest_closed=true")
    empty_reports = [
        _session_quality_report(normalized_market, symbol, session_date, [], evaluated_at)
        for symbol in normalized_symbols
    ]
    if empty_reports and empty_reports[0]["status"] == "NON_SESSION":
        bars_by_symbol = {symbol: [] for symbol in normalized_symbols}
        source_counts_by_symbol = {symbol: {} for symbol in normalized_symbols}
    else:
        if not snapshot_store.enabled():
            raise HTTPException(status_code=503, detail="Market snapshot persistence is disabled")
        try:
            bars_by_symbol = await asyncio.to_thread(
                snapshot_store.load_session_minute_bars_for_symbols,
                normalized_market, normalized_symbols, session_date,
            )
        except psycopg.Error as exc:
            raise HTTPException(status_code=503, detail="Stored market data is unavailable") from exc
        try:
            source_counts_by_symbol = await asyncio.to_thread(
                snapshot_store.load_session_minute_bar_source_counts,
                normalized_market, normalized_symbols, session_date,
            )
        except psycopg.Error as exc:
            raise HTTPException(status_code=503, detail="Stored market-data provenance is unavailable") from exc
    reports = [
        _session_quality_report(
            normalized_market, symbol, session_date, bars_by_symbol.get(symbol, []), evaluated_at,
        ) for symbol in normalized_symbols
    ]
    for report in reports:
        report["source_counts"] = source_counts_by_symbol.get(str(report["symbol"]), {})
    return {"market": normalized_market, "session_date": session_date.isoformat(), "sessions": reports}


@app.get("/api/v1/market/quotes/{market}/{symbol}", response_model=QuoteResponse)
def get_quote(
    market: Market,
    symbol: str = Path(pattern=r"^[A-Za-z0-9.-]{1,16}$"),
    exchange: Exchange | None = Query(default=None),
    client: KisReadOnlyClient = Depends(get_market_data_client),
) -> Quote:
    if market == "US" and exchange is None:
        raise HTTPException(status_code=422, detail="US quotes require exchange=NAS, NYS, or AMS")
    if market == "KR" and not re.fullmatch(r"\d{6}", symbol):
        raise HTTPException(status_code=422, detail="KR symbols must be six digits")
    try:
        quote = client.get_quote(market, symbol, exchange)
        _persist_snapshot(lambda: snapshot_store.store_quote(quote))
        return quote
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KisSharedRateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KisRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/market/candidates/{market}", response_model=CandidateScreenResponse)
async def screen_market_candidates(
    market: Market,
    exchange: Exchange | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=20),
    minimum_turnover: Decimal = Query(gt=0),
    maximum_spread_bps: Decimal = Query(default=Decimal("15"), ge=0),
) -> CandidateScreenResponse:
    """Read-only, top-of-market discovery; output is never an executable signal."""
    if market == "US" and exchange is None:
        raise HTTPException(status_code=422, detail="US candidates require exchange=NAS, NYS, or AMS")
    if market == "KR" and exchange is not None:
        raise HTTPException(status_code=422, detail="KR candidates do not accept an exchange parameter")
    evaluated_at = datetime.now(timezone.utc)
    try:
        window = exchange_continuous_session_window(market, evaluated_at)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Exchange session could not be verified") from exc
    if window is None or not window[0] <= evaluated_at < window[1]:
        raise HTTPException(status_code=409, detail="Candidate screening requires an open continuous trading session")
    try:
        client = get_kis_candidate_client(market)
        candidates = await asyncio.to_thread(
            client.get_ranked_candidates, market, exchange=exchange, limit=limit,
            minimum_turnover=minimum_turnover, maximum_spread_bps=maximum_spread_bps,
        )
        return CandidateScreenResponse(
            market=market, exchange=exchange, currency="KRW" if market == "KR" else "USD",
            minimum_turnover=minimum_turnover, maximum_spread_bps=maximum_spread_bps,
            candidates=[MarketCandidateResponse(**candidate.__dict__) for candidate in candidates],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KisSharedRateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KisRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/v1/market/candidates/{market}/subscribe", response_model=CandidateSubscriptionResponse)
async def subscribe_market_candidates(
    market: Market,
    exchange: Exchange | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=20),
    minimum_turnover: Decimal = Query(gt=0),
    maximum_spread_bps: Decimal = Query(default=Decimal("15"), ge=0),
) -> CandidateSubscriptionResponse:
    """Add screened symbols to the active local PAPER market-data monitor."""
    if os.getenv("PAPER_CANDIDATE_SUBSCRIPTIONS_ENABLED", "false").strip().lower() != "true":
        raise HTTPException(status_code=423, detail="PAPER candidate subscriptions are disabled")
    if (os.getenv("TRADING_MODE", "paper").strip().lower() != "paper"
            or os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower() == "true"):
        raise HTTPException(status_code=423, detail="PAPER mode with live trading disabled is required")
    if market == "US" and exchange is None:
        raise HTTPException(status_code=422, detail="US candidates require exchange=NAS, NYS, or AMS")
    if market == "KR" and exchange is not None:
        raise HTTPException(status_code=422, detail="KR candidates do not accept an exchange parameter")
    monitor_status = paper_session_monitor.status()
    feed_environment = "paper" if market == "KR" else "real"
    if (not monitor_status.get("enabled")
            or monitor_status.get("stream_connections", {}).get(feed_environment) != "RUNNING"):
        raise HTTPException(status_code=409, detail=f"{feed_environment} market monitor must be running")
    evaluated_at = datetime.now(timezone.utc)
    try:
        window = exchange_continuous_session_window(market, evaluated_at)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Exchange session could not be verified") from exc
    if window is None or not window[0] <= evaluated_at < window[1]:
        raise HTTPException(status_code=409, detail="Candidate subscriptions require an open continuous trading session")
    try:
        client = get_kis_candidate_client(market)
        candidates = await asyncio.to_thread(
            client.get_ranked_candidates, market, exchange=exchange, limit=limit,
            minimum_turnover=minimum_turnover, maximum_spread_bps=maximum_spread_bps,
        )
        selected = [candidate for candidate in candidates if candidate.passes_market_filter]
        watches = tuple(
            PaperWatch(candidate.market, candidate.symbol, candidate.exchange)
            for candidate in selected
        )
        result = await paper_session_monitor.add_watches(watches, discovered=True)
        return CandidateSubscriptionResponse(
            market=market, exchange=exchange, considered=len(candidates),
            added=list(result["added"]), subscriptions=int(result["subscriptions"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        status_code = 503 if "KIS WebSocket subscription" in str(exc) else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except KisSharedRateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KisRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/v1/market/candidates/{market}/refresh-subscriptions", response_model=CandidateRefreshResponse)
async def refresh_market_candidate_subscriptions(
    market: Market,
    exchange: Exchange | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=20),
    minimum_turnover: Decimal = Query(gt=0),
    maximum_spread_bps: Decimal = Query(default=Decimal("15"), ge=0),
) -> CandidateRefreshResponse:
    """Refresh candidate-only subscriptions after proving PAPER positions are protected."""
    if os.getenv("PAPER_CANDIDATE_SUBSCRIPTIONS_ENABLED", "false").strip().lower() != "true":
        raise HTTPException(status_code=423, detail="PAPER candidate subscriptions are disabled")
    if os.getenv("PAPER_AUTOTRADING_ENABLED", "false").strip().lower() == "true":
        raise HTTPException(status_code=423, detail="PAPER automation must be disabled during candidate refresh")
    if (os.getenv("TRADING_MODE", "paper").strip().lower() != "paper"
            or os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower() == "true"):
        raise HTTPException(status_code=423, detail="PAPER mode with live trading disabled is required")
    if market == "US" and exchange is None:
        raise HTTPException(status_code=422, detail="US candidates require exchange=NAS, NYS, or AMS")
    if market == "KR" and exchange is not None:
        raise HTTPException(status_code=422, detail="KR candidates do not accept an exchange parameter")
    monitor_status = paper_session_monitor.status()
    feed_environment = "paper" if market == "KR" else "real"
    if (not monitor_status.get("enabled")
            or monitor_status.get("stream_connections", {}).get(feed_environment) != "RUNNING"):
        raise HTTPException(status_code=409, detail=f"{feed_environment} market monitor must be running")
    evaluated_at = datetime.now(timezone.utc)
    try:
        window = exchange_continuous_session_window(market, evaluated_at)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Exchange session could not be verified") from exc
    if window is None or not window[0] <= evaluated_at < window[1]:
        raise HTTPException(status_code=409, detail="Candidate refresh requires an open continuous trading session")
    try:
        client = get_kis_candidate_client(market)
        candidates = await asyncio.to_thread(
            client.get_ranked_candidates, market, exchange=exchange, limit=limit,
            minimum_turnover=minimum_turnover, maximum_spread_bps=maximum_spread_bps,
        )
        desired = tuple(
            PaperWatch(candidate.market, candidate.symbol, candidate.exchange)
            for candidate in candidates if candidate.passes_market_filter
        )
        result = await paper_session_monitor.refresh_discovered_watches(market, desired)
        return CandidateRefreshResponse(
            market=market, exchange=exchange, considered=len(candidates),
            added=list(result["added"]), removed=list(result["removed"]),
            preserved=int(result["preserved"]), open_orders=int(result["open_orders"]),
            subscriptions=int(result["subscriptions"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        status_code = 503 if "could not be verified" in str(exc) or "request failed" in str(exc) or "timed out" in str(exc) else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except KisSharedRateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KisRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/market/bars/KR/{symbol}", response_model=HistoricalMinuteBarsResponse)
def get_domestic_minute_bars(
    symbol: str = Path(pattern=r"^\d{6}$"),
    session_date: date = Query(),
    end_time: str = Query(default="153000", pattern=r"^\d{6}$"),
    client: KisReadOnlyClient = Depends(get_kis_client),
) -> HistoricalMinuteBarsResponse:
    """Fetch one read-only page (up to 120 bars) of domestic KIS minute history."""
    try:
        bars = client.get_domestic_minute_bars(symbol, session_date, end_time)
        _persist_snapshot(lambda: snapshot_store.store_minute_bars(
            "KR", symbol, bars, source="KIS_REST_MINUTE_CHART",
        ))
        return HistoricalMinuteBarsResponse(
            market="KR", symbol=symbol, session_date=session_date, source="KIS_REST_MINUTE_CHART",
            spread_bps_available=False,
            bars=[QuantBarRequest(
                timestamp=bar.timestamp, open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                volume=bar.volume, spread_bps=None,
            ) for bar in bars],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KisSharedRateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KisRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/market/sessions/KR/{symbol}/bars", response_model=HistoricalMinuteBarsResponse)
def get_domestic_minute_session_bars(
    symbol: str = Path(pattern=r"^\d{6}$"),
    session_date: date = Query(),
    end_time: str = Query(default="153000", pattern=r"^\d{6}$"),
    client: KisReadOnlyClient = Depends(get_kis_client),
) -> HistoricalMinuteBarsResponse:
    """Fetch a bounded, exchange-local KRX session prefix using paged read-only KIS requests."""
    try:
        bars = client.get_domestic_minute_session_bars(symbol, session_date, end_time)
        _persist_snapshot(lambda: snapshot_store.store_minute_bars(
            "KR", symbol, bars, source="KIS_REST_MINUTE_SESSION",
        ))
        return HistoricalMinuteBarsResponse(
            market="KR", symbol=symbol, session_date=session_date, source="KIS_REST_MINUTE_SESSION",
            spread_bps_available=False,
            bars=[QuantBarRequest(
                timestamp=bar.timestamp, open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                volume=bar.volume, spread_bps=None,
            ) for bar in bars],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KisSharedRateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KisRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/market/bars/US/{symbol}", response_model=HistoricalMinuteBarsResponse)
def get_overseas_minute_bars(
    symbol: str = Path(pattern=r"^[A-Za-z0-9.-]{1,16}$"),
    exchange: Exchange = Query(),
    session_date: date | None = Query(default=None),
    client: KisReadOnlyClient = Depends(get_kis_real_market_data_client),
) -> HistoricalMinuteBarsResponse:
    """Fetch one latest KIS US minute page (up to 120 bars), optionally date-filtered."""
    normalized_symbol = symbol.upper()
    try:
        bars = client.get_overseas_minute_bars(normalized_symbol, exchange, session_date)
        _persist_snapshot(lambda: snapshot_store.store_minute_bars(
            "US", normalized_symbol, bars, source="KIS_REST_MINUTE_CHART",
        ))
        actual_session = session_date or (bars[0].timestamp.date() if bars else None)
        return HistoricalMinuteBarsResponse(
            market="US", symbol=normalized_symbol, session_date=actual_session,
            source="KIS_REST_MINUTE_CHART", spread_bps_available=False,
            bars=[QuantBarRequest(
                timestamp=bar.timestamp, open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                volume=bar.volume, spread_bps=None,
            ) for bar in bars],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KisSharedRateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KisRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/market/sessions/US/{symbol}/bars", response_model=HistoricalMinuteBarsResponse)
def get_overseas_minute_session_bars(
    symbol: str = Path(pattern=r"^[A-Za-z0-9.-]{1,16}$"),
    exchange: Exchange = Query(),
    session_date: date = Query(),
    client: KisReadOnlyClient = Depends(get_kis_real_market_data_client),
) -> HistoricalMinuteBarsResponse:
    """Fetch one bounded historical US session through the separately gated REAL KIS client."""
    normalized_symbol = symbol.upper()
    try:
        history: HistoricalMinuteSession = client.get_overseas_minute_session_snapshot(
            normalized_symbol, exchange, session_date,
        )
        bars = history.bars
        _persist_snapshot(lambda: snapshot_store.store_minute_bars(
            "US", normalized_symbol, bars, source="KIS_REST_MINUTE_SESSION",
        ))
        return HistoricalMinuteBarsResponse(
            market="US", symbol=normalized_symbol, session_date=session_date,
            source="KIS_REST_MINUTE_SESSION", spread_bps_available=False,
            source_pages_fetched=history.pages_fetched,
            source_records_seen=history.source_record_count,
            source_first_bar_at=history.first_source_timestamp,
            source_last_bar_at=history.last_source_timestamp,
            source_continuation_states=history.continuation_states,
            bars=[QuantBarRequest(
                timestamp=bar.timestamp, open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                volume=bar.volume, spread_bps=None,
            ) for bar in bars],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KisSharedRateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KisRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.websocket("/api/v1/market/stream")
async def stream_market_data(websocket: WebSocket) -> None:
    """Relay one read-only KIS trade stream to a loopback client."""
    settings = kis_stream.StreamSettings.from_environment()
    if not settings.enabled:
        await websocket.close(code=4403, reason="KIS WebSocket is disabled")
        return
    if not settings.kis.app_key or not settings.kis.app_secret:
        await websocket.close(code=4403, reason="KIS credentials are not configured")
        return

    await websocket.accept()
    try:
        request = await websocket.receive_json()
        if not isinstance(request, dict):
            raise ValueError("subscription must be a JSON object")
        market = request.get("market")
        symbol = request.get("symbol")
        exchange = request.get("exchange")
        if not isinstance(symbol, str) or not isinstance(market, str) or (exchange is not None and not isinstance(exchange, str)):
            raise ValueError("market, symbol, and optional exchange must be strings")
        message = kis_stream.subscription_message("pending", market, symbol, exchange)
        key = await asyncio.to_thread(kis_stream.approval_key, settings.kis)
        message["header"]["approval_key"] = key

        import websockets

        async with websockets.connect(settings.websocket_url, ping_interval=30, close_timeout=3) as upstream:
            await upstream.send(json.dumps(message, separators=(",", ":")))
            await websocket.send_json({"kind": "subscribed", "market": market, "symbol": symbol.upper(), "exchange": exchange})
            while True:
                frame = await upstream.recv()
                if await kis_stream.reply_to_pingpong(upstream, frame):
                    continue
                if isinstance(frame, bytes):
                    frame = frame.decode("utf-8", errors="replace")
                await websocket.send_json(kis_stream.normalize_stream_frame(frame))
    except WebSocketDisconnect:
        return
    except ValueError as exc:
        await websocket.send_json({"kind": "error", "message": str(exc)})
        await websocket.close(code=4400)
    except Exception:
        # Upstream protocol details and credential-bearing data are intentionally not returned.
        try:
            await websocket.send_json({"kind": "error", "message": "KIS WebSocket stream is unavailable"})
            await websocket.close(code=1011)
        except Exception:
            pass


def _persist_snapshot(save) -> None:
    if not snapshot_store.enabled():
        return
    try:
        save()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="KIS snapshot storage is unavailable") from exc


@app.get("/api/v1/accounts/{environment}/{market}/balance", response_model=AccountBalanceResponse)
def get_account_balance(
    environment: str = Path(pattern="^(paper|real)$"),
    market: Market = Path(),
    exchange: str = Query(default="NASD", pattern="^(NASD|NYSE|AMEX)$"),
) -> AccountBalance:
    """Read-only KIS balance/positions. Disabled unless explicitly enabled in local env."""
    global_reads_enabled = os.getenv("KIS_ACCOUNT_READ_ENABLED", "false").strip().lower() == "true"
    environment_reads_enabled = os.getenv(
        f"KIS_{environment.upper()}_ACCOUNT_READ_ENABLED", "false"
    ).strip().lower() == "true"
    if not global_reads_enabled or not environment_reads_enabled:
        raise HTTPException(status_code=503, detail="KIS account reads are disabled")
    client = get_kis_account_client(environment)
    try:
        balance = client.get_account_balance(market, overseas_exchange=exchange)
        _persist_snapshot(lambda: snapshot_store.store_account_balance(balance))
        return balance
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KisSharedRateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KisRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/v1/analysis/momentum", response_model=AnalysisResponse)
def analyze_momentum(request: AnalysisRequest) -> AnalysisResponse:
    """A transparent SMA crossover preview. This is not an AI prediction or trade instruction."""
    closes = [bar.close for bar in request.bars]
    short_window = min(5, len(closes) - 1)
    short_average = sum(closes[-short_window:]) / short_window
    long_average = sum(closes) / len(closes)
    signal = Signal.HOLD
    if short_average > long_average * 1.002:
        signal = Signal.BUY
    elif short_average < long_average * 0.998:
        signal = Signal.SELL
    return AnalysisResponse(
        market=request.market,
        symbol=request.symbol.strip().upper(),
        signal=signal,
        confidence=0.0,
        source="P1_SMA_PREVIEW",
        executable=False,
        reason="Experimental moving-average preview only; requires independent risk checks and paper validation.",
    )


@app.post("/api/v1/strategies/orb-vwap/backtest", response_model=QuantBacktestResponse)
def backtest_orb_vwap_endpoint(request: QuantBacktestRequest) -> QuantBacktestResponse:
    """Run a local, long-only one-session backtest. It never creates executable orders."""
    try:
        bars = [MinuteBar(
            timestamp=item.timestamp, open=item.open, high=item.high, low=item.low,
            close=item.close, volume=item.volume, spread_bps=item.spread_bps,
        ) for item in request.bars]
        result = backtest_orb_vwap(request.market, request.symbol, bars, QuantConfig(), quantity=request.quantity)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Exchange calendar is unavailable; backtest was not run") from exc
    return QuantBacktestResponse(
        market=result.market, symbol=result.symbol, session=result.session,
        trades=[trade.__dict__ for trade in result.trades], gross_pnl=result.gross_pnl,
        costs=result.costs, net_pnl=result.net_pnl, data_points=result.data_points,
        signals=result.signals, executable=False,
    )


@app.post("/api/v1/ai/models/{model_id}/predict")
def predict_with_model(
    model_id: str = Path(pattern=r"^(lightgbm|xgboost)-[a-f0-9]{32}$"),
    request: AiPredictionRequest = ...,
) -> dict[str, object]:
    """Return an advisory probability from a locally registered model; never an order."""
    try:
        bars = [MinuteBar(
            timestamp=item.timestamp, open=item.open, high=item.high, low=item.low,
            close=item.close, volume=item.volume, spread_bps=item.spread_bps,
        ) for item in request.bars]
        registry = LocalModelRegistry(os.getenv("AI_MODEL_REGISTRY_PATH", ".local/models"))
        result = registry.predict_preview(model_id, bars, market=request.market.value, symbol=request.symbol)
        result["symbol"] = request.symbol.strip().upper()
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _model_registry() -> LocalModelRegistry:
    return LocalModelRegistry(os.getenv("AI_MODEL_REGISTRY_PATH", ".local/models"))


def _require_model_governance_token(token: str | None = Header(default=None, alias="X-Model-Governance-Token")) -> None:
    expected = os.getenv("MODEL_GOVERNANCE_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="Model governance changes are locked until a local token is configured")
    if token is None or not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Model governance token is invalid")


@app.get("/api/v1/ai/models")
def list_ai_models() -> dict[str, object]:
    registry = _model_registry()
    return {
        "models": registry.list_models(),
        "governance_events": registry.governance_events(30),
        "governance_audit": registry.governance_audit_status(),
        "executable": False,
    }


@app.post("/api/v1/ai/models/{model_id}/approve")
def approve_ai_model(model_id: str, request: ModelApprovalRequest,
                     _authorized: None = Depends(_require_model_governance_token)) -> dict[str, object]:
    try:
        metadata = _model_registry().approve(model_id, approved_by=request.approved_by, note=request.note)
        return {"model_id": model_id, "approved": bool(metadata.get("approved")), "executable": False,
                "approval": metadata.get("approval")}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/ai/models/{model_id}/activate")
def activate_ai_model(model_id: str, request: ModelChangeRequest,
                      _authorized: None = Depends(_require_model_governance_token)) -> dict[str, object]:
    try:
        event = _model_registry().activate(model_id, changed_by=request.changed_by, note=request.note)
        return {**event, "executable": False}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/ai/models/{model_id}/rollback")
def rollback_ai_model(model_id: str, request: ModelChangeRequest,
                      _authorized: None = Depends(_require_model_governance_token)) -> dict[str, object]:
    try:
        event = _model_registry().activate(model_id, changed_by=request.changed_by, note=request.note, action="rollback")
        return {**event, "executable": False}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
