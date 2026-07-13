"""
Thin wrapper around Alpaca's market data API (free real-time IEX feed on
a paper account is sufficient — no live trading is performed by this bot).
"""

from __future__ import annotations
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

import config

_TF_MAP = {
    "1Min": TimeFrame(1, TimeFrameUnit.Minute),
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    "15Min": TimeFrame(15, TimeFrameUnit.Minute),
    "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
}


class DataFeed:
    def __init__(self):
        if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
            raise RuntimeError(
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables."
            )
        self.client = StockHistoricalDataClient(
            config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY
        )

    def get_bars(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        """
        Returns a DataFrame indexed by timestamp with columns
        [open, high, low, close, volume] for the given symbol/timeframe.
        """
        tf = _TF_MAP[timeframe]
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            limit=limit,
        )
        bars = self.client.get_stock_bars(req)
        df = bars.df
        if df.empty:
            return df

        # bars.df is multi-indexed (symbol, timestamp) when using the new SDK
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level=0)

        df = df[["open", "high", "low", "close", "volume"]].copy()
        df.index = pd.to_datetime(df.index)
        return df

    def get_snapshots(self, symbols: list[str]) -> dict:
        """
        Returns latest trade/quote/daily-bar snapshot for a batch of symbols.
        Used by market_universe.py to compute % change / volume for the
        "movers" tier without pulling full bar history for every ticker.
        """
        req = StockSnapshotRequest(symbol_or_symbols=symbols)
        return self.client.get_stock_snapshot(req)
