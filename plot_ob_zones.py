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
    Draws a TradingView-style "Long/Short Position" tool overlay:
      - A shaded GREEN box from entry to target = the profit zone
      - A shaded RED box from entry to stop = the risk zone
      - A dashed entry line spanning the box width
      - A stats label showing entry/target/stop prices, R:R ratio, and
        the % move each level represents
    Spans from the entry bar out to where the trade's outcome resolved
    (or the hold window, if it timed out).
    """
    try:
        entry_idx = df.index.get_loc(trade.entry_at)
    except KeyError:
        return

    width = max(trade.bars_held, 1)
    is_long = trade.direction == "bullish"

    risk = abs(trade.entry_price - trade.stop_price)
    reward = abs(trade.target_price - trade.entry_price)
    risk_pct = risk / trade.entry_price * 100
    reward_pct = reward / trade.entry_price * 100

    # Position sizing (informational only) — sized so that a stop-out risks
    # RISK_PER_TRADE_PCT of ACCOUNT_EQUITY
    risk_dollars_budget = config.ACCOUNT_EQUITY * (config.RISK_PER_TRADE_PCT / 100)
    shares = int(risk_dollars_budget / risk) if risk > 0 else 0
    dollar_risk = shares * risk
    dollar_reward = shares * reward

    # Profit zone (entry -> target) and risk zone (entry -> stop),
    # stacked on whichever side is correct for long vs short
    profit_low = trade.entry_price if is_long else trade.target_price
    profit_high = trade.target_price if is_long else trade.entry_price
    risk_low = trade.stop_price if is_long else trade.entry_price
    risk_high = trade.entry_price if is_long else trade.stop_price

    profit_box = patches.Rectangle(
        (entry_idx, profit_low), width, profit_high - profit_low,
        facecolor="#26a69a", edgecolor="#26a69a", alpha=0.35, linewidth=1, zorder=4,
    )
    risk_box = patches.Rectangle(
        (entry_idx, risk_low), width, risk_high - risk_low,
        facecolor="#ef5350", edgecolor="#ef5350", alpha=0.35, linewidth=1, zorder=4,
    )
    ax.add_patch(profit_box)
    ax.add_patch(risk_box)

    # Entry line
    ax.plot([entry_idx, entry_idx + width], [trade.entry_price, trade.entry_price],
             "-", color="#333333", linewidth=1.2, zorder=5)

    direction_label = "LONG" if is_long else "SHORT"
    outcome_color = {"win": "#26a69a", "loss": "#ef5350", "timeout": "#787b86"}[trade.outcome]
    rr_text = (
        f"{direction_label}  R:R 1:{reward_multiple:.1f}\n"
        f"Target {trade.target_price:.2f} (+{reward_pct:.2f}%, +${dollar_reward:,.0f})\n"
        f"Entry  {trade.entry_price:.2f}\n"
        f"Stop   {trade.stop_price:.2f} (-{risk_pct:.2f}%, -${dollar_risk:,.0f})\n"
        f"Size   {shares:,} sh (risking {config.RISK_PER_TRADE_PCT:.1f}% of ${config.ACCOUNT_EQUITY:,.0f})\n"
        f"Result: {trade.outcome} ({trade.r_multiple:+.1f}R)"
    )
    ax.annotate(
        rr_text,
        xy=(entry_idx + width, trade.entry_price),
        xytext=(6, 0),
        textcoords="offset points",
        fontsize=6.5,
        color="#222222",
        va="center",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor=outcome_color, linewidth=1, alpha=0.9),
        zorder=6,
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
