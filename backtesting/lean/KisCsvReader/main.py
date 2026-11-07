from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone

from QuantConnect import Globals, SubscriptionTransportMedium, TimeZones
from QuantConnect.Data import SubscriptionDataSource
from QuantConnect.Python import PythonData
from AlgorithmImports import *


class KisMinuteBar(PythonData):
    """Read a locally staged KIS minute-bar CSV as LEAN custom data."""

    def get_source(self, config, date, is_live):
        path = os.path.join(Globals.data_folder, "kis-minute-bars.csv")
        return SubscriptionDataSource(path, SubscriptionTransportMedium.LOCAL_FILE)

    def reader(self, config, line, date, is_live):
        if not line or line.startswith("symbol,"):
            return None
        try:
            row = next(csv.reader([line]))
            if len(row) < 7:
                return None
            timestamp = datetime.fromisoformat(row[1])
            if timestamp.tzinfo is None:
                return None
            timestamp_utc = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
            bar = KisMinuteBar()
            bar.symbol = config.symbol
            bar.time = timestamp_utc
            bar.end_time = timestamp_utc + timedelta(minutes=1)
            bar.value = float(row[5])
            return bar
        except (IndexError, TypeError, ValueError):
            return None


class KisCsvReader(QCAlgorithm):
    """LEAN data-ingestion smoke project; it intentionally submits no orders."""

    def initialize(self):
        market = self.get_parameter("market") or "US"
        ticker = self.get_parameter("symbol") or "AAPL"
        session = self.get_parameter("session") or "2026-11-06"
        if market not in ("KR", "US"):
            raise ValueError("market parameter must be KR or US")

        self.set_time_zone(TimeZones.UTC)
        session_date = datetime.strptime(session, "%Y-%m-%d")
        self.set_start_date(session_date.year, session_date.month, session_date.day)
        self.set_end_date(session_date.year, session_date.month, session_date.day)
        self.set_cash(1_000_000)
        self._bars = 0
        self._first = None
        self._last = None
        self._market = market
        self._ticker = ticker.strip().upper()
        self._symbol = self.add_data(KisMinuteBar, self._ticker, Resolution.MINUTE, TimeZones.UTC)

    def on_data(self, data):
        if self._symbol not in data:
            return
        bar = data[self._symbol]
        self._bars += 1
        self._first = self._first or bar.time
        self._last = bar.time

    def on_end_of_algorithm(self):
        self.debug(
            f"KIS_LOCAL_CSV market={self._market} symbol={self._ticker} "
            f"bars={self._bars} first_utc={self._first} last_utc={self._last} orders=0"
        )
