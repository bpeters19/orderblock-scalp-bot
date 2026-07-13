"""
CLI for backtesting the order-block strategy.

Usage examples:

  # Pull real history from Alpaca and backtest a watchlist
  python3 backtest.py --symbols AAPL,MSFT,NVDA --days 90 --timeframe 15Min

  # Backtest a local CSV instead (columns: timestamp,open,high,low,close,volume)
  python3 backtest.py --csv path/to/AAPL_15min.csv --symbol AAPL

Results are printed as a per-symbol summary plus an overall summary, and
a full trade-by-trade log is written to backtest_results.csv.
"""

from __future__ import annotations
import argparse
import sys
import pandas as pd

import config
from backtest_engine import backtest_symbol, summarize


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.set_index("timestamp")
    return df[["open", "high", "low", "close", "volume"]]


def load_from_alpaca(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    from data_feed import DataFeed  # imported lazily so --csv mode needs no Alpaca keys

    feed = DataFeed()
    # Rough bar-count estimate per day depending on timeframe, padded generously
    bars_per_day = {"1Min": 390, "5Min": 78, "15Min": 26, "1Hour": 7}.get(timeframe, 26)
    limit = min(bars_per_day * days + 50, 10000)
    return feed.get_bars(symbol, timeframe, limit=limit)


def main():
    parser = argparse.ArgumentParser(description="Backtest the order-block strategy")
    parser.add_argument("--symbols", type=str, help="Comma-separated symbols (Alpaca mode)")
    parser.add_argument("--csv", type=str, help="Path to a local OHLCV CSV instead of Alpaca")
    parser.add_argument("--symbol", type=str, default="CSV", help="Symbol label when using --csv")
    parser.add_argument("--timeframe", type=str, default=config.STRUCTURE_TF)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--reward-multiple", type=float, default=config.DEFAULT_RISK_REWARD_TARGET)
    parser.add_argument("--max-hold-bars", type=int, default=20)
    parser.add_argument("--out", type=str, default="backtest_results.csv")
    args = parser.parse_args()

    all_results = []

    if args.csv:
        df = load_csv(args.csv)
        results = backtest_symbol(
            df,
            symbol=args.symbol,
            swing_lookback=config.SWING_LOOKBACK,
            min_displacement_atr_mult=config.MIN_DISPLACEMENT_ATR_MULT,
            reward_multiple=args.reward_multiple,
            max_hold_bars=args.max_hold_bars,
        )
        all_results.extend(results)
        print(f"{args.symbol}: {summarize(results)}")

    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
        for symbol in symbols:
            try:
                df = load_from_alpaca(symbol, args.timeframe, args.days)
                if df.empty:
                    print(f"{symbol}: no data returned, skipping")
                    continue
                results = backtest_symbol(
                    df,
                    symbol=symbol,
                    swing_lookback=config.SWING_LOOKBACK,
                    min_displacement_atr_mult=config.MIN_DISPLACEMENT_ATR_MULT,
                    reward_multiple=args.reward_multiple,
                    max_hold_bars=args.max_hold_bars,
                )
                all_results.extend(results)
                print(f"{symbol}: {summarize(results)}")
            except Exception as e:
                print(f"{symbol}: error — {e}")
    else:
        print("Provide either --symbols (Alpaca mode) or --csv (offline mode).")
        sys.exit(1)

    print("\n=== OVERALL ===")
    print(summarize(all_results))

    if all_results:
        rows = [r.__dict__ for r in all_results]
        pd.DataFrame(rows).to_csv(args.out, index=False)
        print(f"\nFull trade log written to {args.out}")


if __name__ == "__main__":
    main()
