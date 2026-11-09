"""KIS public trade-tick parsing and one-minute bar aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .quant import MinuteBar


@dataclass(frozen=True)
class TradeTick:
    market: str
    symbol: str
    timestamp: datetime
    price: Decimal
    volume: int
    bid: Decimal | None
    ask: Decimal | None

    @property
    def spread_bps(self) -> Decimal | None:
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask < self.bid:
            return None
        midpoint = (self.bid + self.ask) / Decimal(2)
        return (self.ask - self.bid) / midpoint * Decimal(10_000) if midpoint else None


@dataclass(frozen=True)
class OrderBookQuote:
    market: str
    symbol: str
    timestamp: datetime
    bid: Decimal | None
    ask: Decimal | None


def _number(value: str, name: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid KIS {name} field") from exc
    if not parsed.is_finite():
        raise ValueError(f"invalid KIS {name} field")
    return parsed


def _parse_time(date_value: str, time_value: str, zone: str) -> datetime:
    day = datetime.strptime(date_value, "%Y%m%d").date()
    clock = datetime.strptime(time_value, "%H%M%S").time()
    return datetime.combine(day, clock, tzinfo=ZoneInfo(zone))


def parse_trade_frame(frame: str, market: str, exchange: str | None = None) -> list[TradeTick]:
    return parse_trade_frame_with_diagnostics(frame, market, exchange)[0]


def parse_trade_frame_with_diagnostics(
    frame: str, market: str, exchange: str | None = None,
) -> tuple[list[TradeTick], int]:
    """Parse official KIS H0STCNT0 or HDFSCNT0 pipe/caret market-data frames.

    Account notices and encrypted frames are rejected. Only market price, volume,
    timestamps and best bid/ask fields are returned. The second result counts
    domestic records excluded because their market-class code is not regular trading.
    """
    parts = frame.split("|", 3)
    if len(parts) != 4 or parts[0] != "0":
        raise ValueError("only unencrypted KIS market-data frames are supported")
    tr_id, count_raw, payload = parts[1], parts[2], parts[3]
    try:
        count = int(count_raw)
    except ValueError as exc:
        raise ValueError("invalid KIS frame record count") from exc
    if count <= 0 or count > 100:
        raise ValueError("KIS frame record count is outside the supported range")
    fields = payload.split("^")
    if market == "KR" and tr_id == "H0STCNT0":
        # KIS's official 47-column schema places VI_STND_PRC at 45 and MARKET_CLS_CODE at 46.
        width, zone = 47, "Asia/Seoul"
        get_tick = lambda row: TradeTick(
            market="KR", symbol=row[0], timestamp=_parse_time(row[33], row[1], zone),
            price=_number(row[2], "price"), volume=abs(int(row[12])),  # CNTG_VOL, not ACML_VOL
            ask=_number(row[10], "ask"), bid=_number(row[11], "bid"),
        )
    elif market == "US" and tr_id == "HDFSCNT0":
        if exchange not in {"NAS", "NYS", "AMS"}:
            raise ValueError("US tick parsing requires exchange NAS, NYS, or AMS")
        width, zone = 26, "America/New_York"
        get_tick = lambda row: TradeTick(
            market="US", symbol=row[1], timestamp=_parse_time(row[3], row[5], zone),
            price=_number(row[11], "price"), volume=abs(int(row[19])),  # per-trade execution volume
            ask=_number(row[16], "ask"), bid=_number(row[15], "bid"),
        )
    else:
        raise ValueError("KIS frame transaction does not match the selected market")
    if len(fields) < width * count:
        raise ValueError("KIS frame did not contain all declared records")
    ticks: list[TradeTick] = []
    non_regular_records = 0
    for index in range(count):
        row = fields[index * width:(index + 1) * width]
        if market == "KR" and row[46].strip() != "2":
            # KIS MARKET_CLS_CODE: only regular-session executions enter strategy bars.
            non_regular_records += 1
            continue
        tick = get_tick(row)
        if tick.price <= 0:
            raise ValueError("KIS trade price must be positive")
        ticks.append(tick)
    return ticks, non_regular_records


def parse_domestic_orderbook_frame(
    frame: str, *, received_at: datetime | None = None,
) -> list[OrderBookQuote]:
    return parse_domestic_orderbook_frame_with_diagnostics(frame, received_at=received_at)[0]


def parse_domestic_orderbook_frame_with_diagnostics(
    frame: str, *, received_at: datetime | None = None,
) -> tuple[list[OrderBookQuote], int]:
    """Parse best bid/ask from official domestic KIS H0STASP0 frames."""
    parts = frame.split("|", 3)
    if len(parts) != 4 or parts[0] != "0" or parts[1] != "H0STASP0":
        raise ValueError("only unencrypted domestic H0STASP0 frames are supported")
    try:
        count = int(parts[2])
    except ValueError as exc:
        raise ValueError("invalid KIS frame record count") from exc
    width = 63
    if count <= 0 or count > 100:
        raise ValueError("KIS frame record count is outside the supported range")
    fields = parts[3].split("^")
    if len(fields) < width * count:
        raise ValueError("KIS orderbook frame did not contain all declared records")
    zone = ZoneInfo("Asia/Seoul")
    reference = received_at or datetime.now(zone)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("KIS orderbook receive time must include a timezone")
    reference = reference.astimezone(zone)
    quotes: list[OrderBookQuote] = []
    non_regular_records = 0
    for index in range(count):
        row = fields[index * width:(index + 1) * width]
        if row[62].strip() != "2":
            # Do not reuse pre/after-market or auction quotes for regular-session bars.
            non_regular_records += 1
            continue
        try:
            clock = datetime.strptime(row[1], "%H%M%S").time()
            timestamp = datetime.combine(reference.date(), clock, zone)
            bid_value = _number(row[13], "best bid")
            ask_value = _number(row[3], "best ask")
        except (ValueError, IndexError):
            continue
        if not row[0].strip():
            continue
        quotes.append(OrderBookQuote(
            "KR", row[0].strip(), timestamp,
            bid_value if bid_value > 0 else None,
            ask_value if ask_value > 0 else None,
        ))
    return quotes, non_regular_records


class MinuteBarAggregator:
    """Aggregate ordered exchange ticks; flushes a bar only when its minute closes."""

    def __init__(self) -> None:
        self._minute: datetime | None = None
        self._symbol: str | None = None
        self._open = self._high = self._low = self._close = Decimal(0)
        self._volume = 0
        self._bid: Decimal | None = None
        self._ask: Decimal | None = None
        self._quote_minute: datetime | None = None
        self._last_tick: datetime | None = None

    def quote_is_stale(self, quote: OrderBookQuote) -> bool:
        if quote.timestamp.tzinfo is None or quote.timestamp.utcoffset() is None:
            raise ValueError("orderbook timestamp must include a timezone")
        minute = quote.timestamp.replace(second=0, microsecond=0)
        return self._minute is not None and minute < self._minute

    def update_quote(self, quote: OrderBookQuote) -> bool:
        if quote.timestamp.tzinfo is None or quote.timestamp.utcoffset() is None:
            raise ValueError("orderbook timestamp must include a timezone")
        if self._symbol is not None and quote.symbol != self._symbol:
            raise ValueError("an aggregator instance accepts one symbol at a time")
        minute = quote.timestamp.replace(second=0, microsecond=0)
        if self.quote_is_stale(quote):
            return False
        valid_quote = (
            quote.bid is not None and quote.ask is not None
            and quote.bid > 0 and quote.ask >= quote.bid
        )
        if not valid_quote:
            if self._quote_minute == minute and self._bid is not None and self._ask is not None:
                if self._bid > 0 and self._ask >= self._bid:
                    # Keep the last usable quote for this minute when KIS sends
                    # an empty or crossed update; never carry it into a later minute.
                    return False
            self._symbol = quote.symbol
            self._quote_minute = minute
            self._bid = self._ask = None
            return True
        self._symbol = quote.symbol
        self._quote_minute = minute
        self._bid, self._ask = quote.bid, quote.ask
        return True

    def add(self, tick: TradeTick) -> MinuteBar | None:
        minute = tick.timestamp.replace(second=0, microsecond=0)
        if self._last_tick is not None and tick.timestamp < self._last_tick:
            raise ValueError("trade ticks must be chronological")
        if self._symbol is not None and tick.symbol != self._symbol:
            raise ValueError("an aggregator instance accepts one symbol at a time")
        self._symbol = tick.symbol
        self._last_tick = tick.timestamp
        completed: MinuteBar | None = None
        if self._minute is not None and minute > self._minute:
            completed = self._build_bar()
            self._minute = minute
            self._open = self._high = self._low = self._close = tick.price
            self._volume = tick.volume
            if self._quote_minute is None or self._quote_minute < minute:
                self._bid = self._ask = None
                self._quote_minute = None
        elif self._minute is None:
            self._minute = minute
            self._open = self._high = self._low = self._close = tick.price
            self._volume = tick.volume
        else:
            self._high = max(self._high, tick.price)
            self._low = min(self._low, tick.price)
            self._close = tick.price
            self._volume += tick.volume
        if (tick.bid is not None and tick.ask is not None
                and tick.bid > 0 and tick.ask >= tick.bid):
            self.update_quote(OrderBookQuote(tick.market, tick.symbol, tick.timestamp, tick.bid, tick.ask))
        return completed

    def flush(self) -> MinuteBar | None:
        if self._minute is None:
            return None
        bar = self._build_bar()
        self._minute = None
        self._symbol = None
        self._last_tick = None
        self._quote_minute = None
        self._bid = self._ask = None
        return bar

    def _build_bar(self) -> MinuteBar:
        spread: Decimal | None = None
        if (self._quote_minute == self._minute and self._bid is not None and self._ask is not None
                and self._bid > 0 and self._ask >= self._bid):
            mid = (self._bid + self._ask) / Decimal(2)
            spread = (self._ask - self._bid) / mid * Decimal(10_000)
        return MinuteBar(self._minute, self._open, self._high, self._low, self._close, self._volume, spread)
