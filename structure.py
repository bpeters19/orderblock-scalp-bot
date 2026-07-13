"""
Market structure detection.

Implements the price-action mechanics behind order-block trading:
  - Swing high / swing low detection
  - Break of Structure (BOS): price closes beyond the most recent
    swing high (bullish) or swing low (bearish) in the direction of trend
  - Change of Character (CHoCH): the first break of structure AGAINST
    the prevailing trend — signals a possible reversal
  - Average True Range (ATR) for measuring "displacement" (does the
    breakout candle/leg have enough force to be institutional, not noise)

All functions take/return pandas DataFrames with columns:
    ['open', 'high', 'low', 'close', 'volume'] indexed by timestamp.
"""

from __future__ import annotations
import pandas as pd
import numpy as np


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def find_swings(df: pd.DataFrame, lookback: int = 3) -> pd.DataFrame:
    """
    Marks swing highs/lows using a simple fractal: a bar is a swing high
    if its high is the max within +/- lookback bars (and similarly for lows).
    Adds boolean columns 'swing_high' and 'swing_low' to a copy of df.
    """
    out = df.copy()
    highs, lows = out["high"].values, out["low"].values
    n = len(out)
    swing_high = np.zeros(n, dtype=bool)
    swing_low = np.zeros(n, dtype=bool)

    for i in range(lookback, n - lookback):
        window_h = highs[i - lookback:i + lookback + 1]
        window_l = lows[i - lookback:i + lookback + 1]
        if highs[i] == window_h.max() and np.argmax(window_h) == lookback:
            swing_high[i] = True
        if lows[i] == window_l.min() and np.argmin(window_l) == lookback:
            swing_low[i] = True

    out["swing_high"] = swing_high
    out["swing_low"] = swing_low
    return out


def label_structure(df: pd.DataFrame, lookback: int = 3) -> pd.DataFrame:
    """
    Walks the series of swing highs/lows and labels each bar with:
      - 'trend'      : current inferred trend ('up', 'down', or None early on)
      - 'event'      : 'BOS' if this bar breaks structure WITH the trend,
                        'CHoCH' if it breaks structure AGAINST the trend
                        (i.e. a likely reversal), else None
      - 'ref_swing'  : the swing level (price) that was broken, for reference

    This is intentionally simple/mechanical (matches the rules-based style
    order-block traders use) rather than a full proprietary SMC engine.
    """
    out = find_swings(df, lookback=lookback)
    trend = None
    last_swing_high = None
    last_swing_low = None

    events = [None] * len(out)
    ref_swings = [np.nan] * len(out)
    trends = [None] * len(out)

    closes = out["close"].values
    sh = out["swing_high"].values
    sl = out["swing_low"].values

    for i in range(len(out)):
        # Update tracked swing levels as they're confirmed
        if sh[i]:
            last_swing_high = out["high"].values[i]
        if sl[i]:
            last_swing_low = out["low"].values[i]

        close = closes[i]
        event = None
        ref = np.nan

        if last_swing_high is not None and close > last_swing_high:
            # Bullish break
            if trend in (None, "up"):
                event = "BOS"
            else:
                event = "CHoCH"
            ref = last_swing_high
            trend = "up"
            # once broken, that swing level is consumed
            last_swing_high = None

        elif last_swing_low is not None and close < last_swing_low:
            # Bearish break
            if trend in (None, "down"):
                event = "BOS"
            else:
                event = "CHoCH"
            ref = last_swing_low
            trend = "down"
            last_swing_low = None

        events[i] = event
        ref_swings[i] = ref
        trends[i] = trend

    out["trend"] = trends
    out["event"] = events
    out["ref_swing"] = ref_swings
    return out
