"""
Plots a symbol's candlestick chart with detected order-block zones and
their OTE (Optimal Trade Entry) bands overlaid, so you can eyeball whether
the zones the logic is flagging actually make visual sense.

Usage:

  # Alpaca mode
  python3 plot_ob_zones.py --symbol AAPL --timeframe 15Min --days 30

  # Offline CSV mode (columns: timestamp,open,high,low,close,volume)
  python3 plot_ob_zones.py --csv path/to/AAPL_15min.csv --symbol AAPL

Saves a PNG (default: <symbol>_ob_zones.png) instead of trying to open an
interactive window, since this is typically run on a headless droplet.
"""

from __future__ import annotations
import argparse
import matplotlib
matplotlib.use("Agg")  # headless-safe backend — no display required
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import mplfinance as mpf
import pandas as pd

import config
from order_blocks import find_order_blocks
from backtest import load_csv, load_from_alpaca
from backtest_engine import backtest_symbol


def _ob_end_index(df: pd.DataFrame, ob, ob_idx: int) -> int:
    """
    Returns the bar index where the zone's rectangle should stop being
    drawn — either the first bar it's mitigated, or the last bar of the
    chart if it's still live.
    """
    after = df.iloc[ob_idx + 1:]
    if ob.direction == "bullish":
        mit = after.index[after["close"] < ob.zone_low]
    else:
        mit = after.index[after["close"] > ob.zone_high]

    if len(mit) > 0:
        return df.index.get_loc(mit[0])
    return len(df) - 1


def _draw_trade_projection(ax, df: pd.DataFrame, trade, reward_multiple: float):
    """
    Draws a projected long/short trade on the chart: entry marker, stop-loss
    line (red), take-profit line (green), spanning from the entry bar out to
    either the bar where the outcome resolved or `max_hold_bars` bars ahead.
    """
    try:
        entry_idx = df.index.get_loc(trade.entry_at)
    except KeyError:
        return

    end_idx = min(entry_idx + max(trade.bars_held, 1), len(df) - 1)
    is_long = trade.direction == "bullish"
    direction_label = "LONG" if is_long else "SHORT"
    marker = "^" if is_long else "v"
    marker_color = "#1f77b4"

    # Entry marker
    ax.plot(entry_idx, trade.entry_price, marker=marker, markersize=10,
             color=marker_color, zorder=5)

    # Stop-loss line (red) and take-profit line (green)
    ax.plot([entry_idx, end_idx], [trade.stop_price, trade.stop_price],
             "-", color="#d62728", linewidth=1.3, alpha=0.85)
    ax.plot([entry_idx, end_idx], [trade.target_price, trade.target_price],
             "-", color="#2ca02c", linewidth=1.3, alpha=0.85)

    outcome_color = {"win": "#2ca02c", "loss": "#d62728", "timeout": "#7f7f7f"}[trade.outcome]
    ax.annotate(
        f"{direction_label} ({trade.outcome}, {trade.r_multiple:+.1f}R)",
        xy=(entry_idx, trade.entry_price),
        xytext=(5, -12 if is_long else 10),
        textcoords="offset points",
        fontsize=7,
        color=outcome_color,
        weight="bold",
    )


def plot_symbol(
    df: pd.DataFrame,
    symbol: str,
    out_path: str,
    max_zones: int = 15,
    show_trades: bool = False,
    reward_multiple: float = config.DEFAULT_RISK_REWARD_TARGET,
    max_hold_bars: int = 20,
):
    blocks = find_order_blocks(
        df,
        symbol=symbol,
        swing_lookback=config.SWING_LOOKBACK,
        min_displacement_atr_mult=config.MIN_DISPLACEMENT_ATR_MULT,
        max_age_bars=len(df),  # show all zones found across the full chart
    )
    # Keep the most recent N so the chart doesn't get cluttered
    blocks = sorted(blocks, key=lambda b: b.formed_at)[-max_zones:]

    fig, axlist = mpf.plot(
        df,
        type="candle",
        style="yahoo",
        volume=False,
        returnfig=True,
        figsize=(16, 8),
        title=f"{symbol} — Order Block Zones ({config.STRUCTURE_TF})",
    )
    ax = axlist[0]

    for ob in blocks:
        try:
            ob_idx = df.index.get_loc(ob.formed_at)
        except KeyError:
            continue
        end_idx = _ob_end_index(df, ob, ob_idx)
        width = max(end_idx - ob_idx, 1)

        color = "#2ca02c" if ob.direction == "bullish" else "#d62728"

        # Order block zone rectangle
        rect = patches.Rectangle(
            (ob_idx, ob.zone_low),
            width,
            ob.zone_high - ob.zone_low,
            linewidth=1,
            edgecolor=color,
            facecolor=color,
            alpha=0.18,
        )
        ax.add_patch(rect)

        # OTE band (dashed lines) within the same x-range
        ote_lo, ote_hi = sorted([ob.ote_low, ob.ote_high])
        ax.plot([ob_idx, ob_idx + width], [ote_lo, ote_lo], "--", color=color, linewidth=0.8, alpha=0.7)
        ax.plot([ob_idx, ob_idx + width], [ote_hi, ote_hi], "--", color=color, linewidth=0.8, alpha=0.7)

        label = "BUY OB" if ob.direction == "bullish" else "SELL OB"
        ax.annotate(
            label,
            xy=(ob_idx, ob.zone_high if ob.direction == "bullish" else ob.zone_low),
            xytext=(3, 3),
            textcoords="offset points",
            fontsize=7,
            color=color,
            weight="bold",
        )

    trade_count = 0
    if show_trades:
        trades = backtest_symbol(
            df,
            symbol=symbol,
            swing_lookback=config.SWING_LOOKBACK,
            min_displacement_atr_mult=config.MIN_DISPLACEMENT_ATR_MULT,
            max_age_bars=len(df),
            reward_multiple=reward_multiple,
            max_hold_bars=max_hold_bars,
        )
        for trade in trades:
            _draw_trade_projection(ax, df, trade, reward_multiple)
        trade_count = len(trades)

    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    suffix = f", {trade_count} trade projection(s)" if show_trades else ""
    print(f"Saved chart to {out_path} ({len(blocks)} zone(s) plotted{suffix})")


def main():
    parser = argparse.ArgumentParser(description="Plot order block zones on a price chart")
    parser.add_argument("--symbol", type=str, required=True)
    parser.add_argument("--csv", type=str, help="Path to a local OHLCV CSV instead of Alpaca")
    parser.add_argument("--timeframe", type=str, default=config.STRUCTURE_TF)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max-zones", type=int, default=15)
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument("--show-trades", action="store_true",
                         help="Overlay projected long/short entries with stop/target lines "
                              "(runs the same simulation as backtest.py)")
    parser.add_argument("--reward-multiple", type=float, default=config.DEFAULT_RISK_REWARD_TARGET)
    parser.add_argument("--max-hold-bars", type=int, default=20)
    args = parser.parse_args()

    if args.csv:
        df = load_csv(args.csv)
    else:
        df = load_from_alpaca(args.symbol, args.timeframe, args.days)

    if df.empty:
        print("No data returned — nothing to plot.")
        return

    out_path = args.out or f"{args.symbol}_ob_zones.png"
    plot_symbol(
        df, args.symbol, out_path,
        max_zones=args.max_zones,
        show_trades=args.show_trades,
        reward_multiple=args.reward_multiple,
        max_hold_bars=args.max_hold_bars,
    )


if __name__ == "__main__":
    main()
