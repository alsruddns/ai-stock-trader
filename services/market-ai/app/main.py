from enum import StrEnum
from functools import lru_cache
import os
import re
import threading
from datetime import date, datetime
from decimal import Decimal
import asyncio
import json
import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, WebSocket, WebSocketDisconnect
import psycopg
from pydantic import BaseModel, Field
from .kis import (AccountBalance, Exchange, KisApiError, KisReadOnlyClient, KisSettings,
                  KisSharedRateLimitUnavailable, Market, Quote)
from . import snapshot_store
from . import kis_stream
from .ai_pipeline import LocalModelRegistry
from .quant import MinuteBar, QuantConfig, backtest_orb_vwap
from .paper_runtime import paper_session_monitor

_KIS_ACCOUNT_CLIENTS: dict[str, KisReadOnlyClient] = {}
_KIS_ACCOUNT_CLIENTS_LOCK = threading.RLock()


@asynccontextmanager
async def lifespan(_app):
    await paper_session_monitor.start()
    try:
        yield
    finally:
        try:
            await paper_session_monitor.stop()
        finally:
            try:
                close_kis_account_clients()
            finally:
                close_kis_market_client()

app = FastAPI(
    title="AI Stock Trader Market Analysis",
    version="0.1.0",
    description="Market analysis and opt-in local paper automation. It has no KIS order route.",
    lifespan=lifespan,
)


@lru_cache
def get_kis_client() -> KisReadOnlyClient:
    settings = KisSettings.from_environment()
    if not settings.enabled:
        raise HTTPException(status_code=503, detail="KIS market data is disabled")
    if not settings.app_key or not settings.app_secret:
        raise HTTPException(status_code=503, detail="KIS market data credentials are not configured")
    return KisReadOnlyClient(settings)


def get_kis_account_client(environment: str) -> KisReadOnlyClient:
    """Reuse one token-caching HTTP client per account environment."""
    settings = KisSettings.from_environment(environment)  # type: ignore[arg-type]
    if (not settings.app_key or not settings.app_secret
            or not re.fullmatch(r"\d{10}", settings.account_number)):
        raise HTTPException(status_code=503, detail="KIS account credentials are not configured")
    with _KIS_ACCOUNT_CLIENTS_LOCK:
        current = _KIS_ACCOUNT_CLIENTS.get(environment)
        if current is not None and current.settings == settings:
            return current
        if current is not None:
            current.close()
        client = KisReadOnlyClient(settings)
        _KIS_ACCOUNT_CLIENTS[environment] = client
        return client


def close_kis_account_clients() -> None:
    with _KIS_ACCOUNT_CLIENTS_LOCK:
        clients = list(_KIS_ACCOUNT_CLIENTS.values())
        _KIS_ACCOUNT_CLIENTS.clear()
    for client in clients:
        client.close()


def close_kis_market_client() -> None:
    try:
        if get_kis_client.cache_info().currsize:
            get_kis_client().close()
    finally:
        get_kis_client.cache_clear()


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


class AccountBalanceResponse(BaseModel):
    environment: str
    market: Market
    currency: str
    summary: dict[str, object]
    positions: list[dict[str, object]]
    retrieved_at: datetime
    source: str


@app.get("/api/v1/health")
def health() -> dict[str, str | bool]:
    settings = KisSettings.from_environment()
    return {
        "status": "ok",
        "mode": "read-only-preview",
        "broker_connected": False,
        "kis_market_data_enabled": settings.enabled,
        "paper_session_monitor": paper_session_monitor.status()["state"],
        "paper_automation_enabled": False,
        "live_trading_enabled": False,
    }


@app.get("/api/v1/paper/session")
def paper_session_status() -> dict[str, object]:
    """Expose the local paper feed monitor without secrets or order controls."""
    return paper_session_monitor.status()


@app.get("/api/v1/market/quotes/{market}/{symbol}", response_model=QuoteResponse)
def get_quote(
    market: Market,
    symbol: str = Path(pattern=r"^[A-Za-z0-9.-]{1,16}$"),
    exchange: Exchange | None = Query(default=None),
    client: KisReadOnlyClient = Depends(get_kis_client),
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
    except KisApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/market/bars/US/{symbol}", response_model=HistoricalMinuteBarsResponse)
def get_overseas_minute_bars(
    symbol: str = Path(pattern=r"^[A-Za-z0-9.-]{1,16}$"),
    exchange: Exchange = Query(),
    session_date: date | None = Query(default=None),
    client: KisReadOnlyClient = Depends(get_kis_client),
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
        result = registry.predict_preview(model_id, bars, market=request.market.value)
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
    return {"models": registry.list_models(), "governance_events": registry.governance_events(30), "executable": False}


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
