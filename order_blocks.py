"""
Order block detection built on top of structure.py.

Mechanical definition used here (matches the mainstream SMC / order-block
rule set: last opposing candle before a displacement move that causes a
BOS or CHoCH):

  Bullish order block:
    - The last BEARISH (close < open) candle immediately before a
      bullish BOS/CHoCH event
    - The move away from it must be a "displacement" — its range should
      exceed MIN_DISPLACEMENT_ATR_MULT * ATR (filters out weak, low-
      conviction breaks)
    - Zone = [candle.low, candle.high] of that opposing candle

  Bearish order block: mirror of the above with the last BULLISH candle
  before a bearish BOS/CHoCH.

An order block is "mitigated" (no longer valid) once price trades back
through the FAR side of the zone after formation (i.e. fully closes
through it), or once price has already tapped and left the zone once
(single-use, matches "if it's already been tapped, it's off the table").

OTE (Optimal Trade Entry) is computed from the impulse leg that created
the BOS/CHoCH: the 62%-79% retracement of that leg. A zone alert is only
considered high quality if the order block zone overlaps the OTE band.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List
import pandas as pd

from structure import label_structure, atr


@dataclass
class OrderBlock:
    symbol: str
    direction: str            # 'bullish' or 'bearish'
    zone_low: float
    zone_high: float
    formed_at: pd.Timestamp
    event: str                 # 'BOS' or 'CHoCH'
    leg_start: float            # price where the impulse leg began (for OTE)
    leg_end: float               # price where the impulse leg ended (swing broken)
    ote_low: float = field(init=False)
    ote_high: float = field(init=False)
    mitigated: bool = False
    tapped: bool = False

    def __post_init__(self):
        lo, hi = sorted([self.leg_start, self.leg_end])
        rng = hi - lo
        if self.direction == "bullish":
            # retrace down from the high of the leg
            self.ote_high = hi - rng * 0.62
            self.ote_low = hi - rng * 0.79
        else:
            self.ote_high = lo + rng * 0.79
            self.ote_low = lo + rng * 0.62

    @property
    def overlaps_ote(self) -> bool:
        lo = max(self.zone_low, min(self.ote_low, self.ote_high))
        hi = min(self.zone_high, max(self.ote_low, self.ote_high))
        return lo <= hi

    def contains(self, price: float) -> bool:
        return self.zone_low <= price <= self.zone_high


def find_order_blocks(
    df: pd.DataFrame,
    symbol: str,
    swing_lookback: int = 3,
    min_displacement_atr_mult: float = 1.5,
    max_age_bars: int = 40,
) -> List[OrderBlock]:
    """
    Scans a structure-labeled dataframe (open/high/low/close/volume) and
    returns unmitigated order blocks formed within the last `max_age_bars`.
    """
    labeled = label_structure(df, lookback=swing_lookback)
    labeled["atr"] = atr(labeled)

    blocks: List[OrderBlock] = []
    n = len(labeled)

    for i in range(1, n):
        event = labeled["event"].iloc[i]
        if event not in ("BOS", "CHoCH"):
            continue

        # Age filter
        if (n - 1 - i) > max_age_bars:
            continue

        direction = "bullish" if labeled["trend"].iloc[i] == "up" else "bearish"

        # Displacement filter: the breaking candle's range vs ATR
        candle_range = labeled["high"].iloc[i] - labeled["low"].iloc[i]
        if candle_range < min_displacement_atr_mult * labeled["atr"].iloc[i]:
            continue

        # Walk backwards from i-1 to find the last OPPOSING candle
        ob_idx = None
        for j in range(i - 1, max(i - 15, -1), -1):
            is_bear_candle = labeled["close"].iloc[j] < labeled["open"].iloc[j]
            is_bull_candle = labeled["close"].iloc[j] > labeled["open"].iloc[j]
            if direction == "bullish" and is_bear_candle:
                ob_idx = j
                break
            if direction == "bearish" and is_bull_candle:
                ob_idx = j
                break
        if ob_idx is None:
            continue

        zone_low = labeled["low"].iloc[ob_idx]
        zone_high = labeled["high"].iloc[ob_idx]

        # Impulse leg for OTE: from the OB candle's close to the swing that
        # was just broken (ref_swing at the event bar)
        leg_start = labeled["close"].iloc[ob_idx]
        leg_end = labeled["ref_swing"].iloc[i]
        if pd.isna(leg_end):
            continue

        ob = OrderBlock(
            symbol=symbol,
            direction=direction,
            zone_low=float(zone_low),
            zone_high=float(zone_high),
            formed_at=labeled.index[ob_idx],
            event=event,
            leg_start=float(leg_start),
            leg_end=float(leg_end),
        )

        # Mitigation check: has price already fully closed through the zone,
        # or tapped through it, between formation and the most recent bar?
        after = labeled.iloc[ob_idx + 1:]
        if ob.direction == "bullish":
            mitigated = (after["close"] < ob.zone_low).any()
            tapped = (after["low"] <= ob.zone_high).any()
        else:
            mitigated = (after["close"] > ob.zone_high).any()
            tapped = (after["high"] >= ob.zone_low).any()

        ob.mitigated = bool(mitigated)
        ob.tapped = bool(tapped)

        if not ob.mitigated:
            blocks.append(ob)

    return blocks
