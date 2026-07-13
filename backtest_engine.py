"""
Backtest engine for the order-block strategy.

IMPORTANT — this runs a SIMPLIFIED single-timeframe simulation, not the
full 3-timeframe (15m zone / 5m tap / 1m confirm) model the live bot uses:

  1. Find order blocks on the given timeframe (same logic as live)
  2. Walk forward bar-by-bar from formation looking for the first bar where
     price trades back into the (still-unmitigated) zone — the "tap"
  3. Require the NEXT bar to close in the trade direction as a lightweight
     stand-in for lower-timeframe confirmation
  4. Simulate a trade from that confirmation bar's close:
       risk   = distance from entry to the far side of the zone
       target = entry +/- (risk * reward_multiple)
     walk forward up to `max_hold_bars` bars to see whether target or
     stop is hit first (first one touched wins; simultaneous touch in the
     same bar counts as a loss, the conservative assumption)

Because the live bot additionally requires an actual lower-timeframe
BOS/CHoCH before alerting, real signal quality should be EQUAL OR BETTER
than what this backtest shows — treat these numbers as a lower-bound
sanity check on the core order-block logic, not a performance guarantee.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
import pandas as pd

from order_blocks import find_order_blocks, OrderBlock


@dataclass
class TradeResult:
    symbol: str
    direction: str
    formed_at: pd.Timestamp
    tap_at: pd.Timestamp
    entry_at: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    outcome: str          # 'win', 'loss', 'timeout'
    r_multiple: float
    bars_held: int


def _first_tap_and_confirm(df: pd.DataFrame, ob: OrderBlock, ob_idx: int):
    """
    Scans forward from the order block's formation bar for:
      (a) the first bar where price trades into the zone (the "tap"), and
      (b) the following bar closing in the trade's direction (the "confirm")
    Stops early if the zone becomes mitigated before a tap+confirm occurs.
    Returns (tap_idx, confirm_idx) or (None, None) if no valid setup found.
    """
    n = len(df)
    for i in range(ob_idx + 1, n - 1):
        row = df.iloc[i]

        # Mitigation check — once price closes clean through the zone,
        # it's off the table.
        if ob.direction == "bullish" and row["close"] < ob.zone_low:
            return None, None
        if ob.direction == "bearish" and row["close"] > ob.zone_high:
            return None, None

        touched = row["low"] <= ob.zone_high and row["high"] >= ob.zone_low
        if not touched:
            continue

        confirm_row = df.iloc[i + 1]
        if ob.direction == "bullish" and confirm_row["close"] > confirm_row["open"]:
            return i, i + 1
        if ob.direction == "bearish" and confirm_row["close"] < confirm_row["open"]:
            return i, i + 1
        # touched but no confirmation on the very next bar — keep scanning;
        # the zone may get tapped again before it's mitigated

    return None, None


def _simulate_trade(
    df: pd.DataFrame,
    ob: OrderBlock,
    confirm_idx: int,
    reward_multiple: float,
    max_hold_bars: int,
) -> TradeResult | None:
    entry_row = df.iloc[confirm_idx]
    entry_price = float(entry_row["close"])

    if ob.direction == "bullish":
        stop_price = ob.zone_low
        risk = entry_price - stop_price
        if risk <= 0:
            return None
        target_price = entry_price + risk * reward_multiple
    else:
        stop_price = ob.zone_high
        risk = stop_price - entry_price
        if risk <= 0:
            return None
        target_price = entry_price - risk * reward_multiple

    outcome = "timeout"
    r_multiple = 0.0
    bars_held = 0

    n = len(df)
    for j in range(confirm_idx + 1, min(confirm_idx + 1 + max_hold_bars, n)):
        row = df.iloc[j]
        bars_held = j - confirm_idx

        if ob.direction == "bullish":
            hit_target = row["high"] >= target_price
            hit_stop = row["low"] <= stop_price
        else:
            hit_target = row["low"] <= target_price
            hit_stop = row["high"] >= stop_price

        if hit_target and hit_stop:
            outcome, r_multiple = "loss", -1.0  # conservative: stop wins ties
            break
        if hit_stop:
            outcome, r_multiple = "loss", -1.0
            break
        if hit_target:
            outcome, r_multiple = "win", reward_multiple
            break

    return TradeResult(
        symbol=ob.symbol,
        direction=ob.direction,
        formed_at=ob.formed_at,
        tap_at=df.index[confirm_idx - 1],
        entry_at=df.index[confirm_idx],
        entry_price=entry_price,
        stop_price=float(stop_price),
        target_price=float(target_price),
        outcome=outcome,
        r_multiple=r_multiple,
        bars_held=bars_held,
    )


def backtest_symbol(
    df: pd.DataFrame,
    symbol: str,
    swing_lookback: int = 3,
    min_displacement_atr_mult: float = 1.5,
    max_age_bars: int = 40,
    reward_multiple: float = 2.0,
    max_hold_bars: int = 20,
) -> list[TradeResult]:
    """
    Runs the full backtest for one symbol's historical bars (single
    timeframe) and returns a list of simulated TradeResults.
    """
    # We need order blocks as they existed at each point in time, not just
    # the final unmitigated set — so re-derive formation points directly
    # rather than relying only on find_order_blocks' end-of-data mitigation
    # filter. find_order_blocks already walks the whole series for BOS/CHoCH
    # events, so we can reuse it to get formation bars/zones, then run our
    # own forward tap/confirm/outcome simulation independent of its final
    # mitigated flag (which reflects status only as of the LAST bar).
    all_blocks = find_order_blocks(
        df,
        symbol=symbol,
        swing_lookback=swing_lookback,
        min_displacement_atr_mult=min_displacement_atr_mult,
        max_age_bars=len(df),  # don't age-filter here; we want every formation
    )

    results: list[TradeResult] = []
    for ob in all_blocks:
        try:
            ob_idx = df.index.get_loc(ob.formed_at)
        except KeyError:
            continue
        if not ob.overlaps_ote:
            continue

        tap_idx, confirm_idx = _first_tap_and_confirm(df, ob, ob_idx)
        if confirm_idx is None:
            continue

        trade = _simulate_trade(df, ob, confirm_idx, reward_multiple, max_hold_bars)
        if trade:
            results.append(trade)

    return results


def summarize(results: list[TradeResult]) -> dict:
    if not results:
        return {"count": 0}

    wins = [r for r in results if r.outcome == "win"]
    losses = [r for r in results if r.outcome == "loss"]
    timeouts = [r for r in results if r.outcome == "timeout"]
    decided = wins + losses

    win_rate = len(wins) / len(decided) * 100 if decided else 0.0
    avg_r = sum(r.r_multiple for r in results) / len(results)
    expectancy = avg_r  # same thing here since timeouts count as 0R

    return {
        "count": len(results),
        "wins": len(wins),
        "losses": len(losses),
        "timeouts": len(timeouts),
        "win_rate_pct": round(win_rate, 1),
        "avg_r_multiple": round(avg_r, 3),
        "expectancy_r": round(expectancy, 3),
    }
