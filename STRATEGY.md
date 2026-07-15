# Order Block Scalping Strategy — Spec

Repo: https://github.com/bpeters19/orderblock-scalp-bot

This is a mechanical rules-based implementation of Smart Money Concepts
(SMC) / order block trading, in the style taught by YouTube channel
**The Trading Geek (Brad Goh)**. It scans stocks, detects valid order
block zones, and sends a Telegram alert when price taps a zone with
lower-timeframe confirmation. **It only reads data and alerts — it never
places trades.**

## Core concept: what is an order block?

A bullish order block is the **last bearish (down) candle right before a
sharp, structure-breaking move up**. It marks the last footprint of
selling before institutional buying took over. The zone (that candle's
high-to-low range) is where price is expected to react if it comes back
to retest it. Bearish order blocks are the mirror image (last bullish
candle before a sharp break down).

## Mechanical rules

1. **Identify swing highs/lows** using a fractal: a bar is a swing
   high/low if it's the extreme within N bars on each side (default N=3).

2. **Detect structure breaks:**
   - **BOS (Break of Structure)** — price closes beyond the last swing
     point *in the direction of* the current trend (continuation).
   - **CHoCH (Change of Character)** — price closes beyond the last swing
     point *against* the current trend (possible reversal).

3. **Displacement filter** — the candle that breaks structure must have a
   range >= `1.5x` the current ATR(14). This filters out weak, low-
   conviction breaks that aren't real institutional moves — just noise.

4. **Find the order block** — walk backward from the BOS/CHoCH candle to
   the last opposing candle (bearish candle before a bullish break, or
   vice versa). That candle's high/low defines the **zone**.

5. **OTE (Optimal Trade Entry)** — compute the 62%–79% Fibonacci
   retracement of the impulse leg (from the order block to the swing
   point it broke). A zone is only considered high-quality if it
   overlaps this OTE band.

6. **Mitigation** — a zone is invalidated once price **closes** clean
   through the far side of it. Once mitigated, it's off the table
   permanently (single-use zones, not re-tradeable).

7. **Age filter** — ignore zones older than 40 bars on the structure
   timeframe (stale zones are lower quality).

## Multi-timeframe model (live scanner)

| Role | Timeframe | Purpose |
|---|---|---|
| Structure / zone identification | 15-min | Where the order block itself lives |
| Tap confirmation | 5-min | Has price actually retraced into the zone? |
| Entry trigger | 1-min | Require a mini BOS/CHoCH in the same direction inside the zone before alerting |

This keeps alert frequency reasonable (a handful of real setups/day per
symbol) instead of firing on every noisy 1-min wiggle.

## Watchlist — two tiers

- **Tier 1 (core)**: S&P 500 + Nasdaq 100 constituents — liquid, cleaner
  structure, lower false-positive rate.
- **Tier 2 (movers)**: top gainers/losers filtered by price (>$5) and
  volume (>1M avg), pct-change threshold — catches volatile momentum
  names, tagged separately in alerts so position sizing can adjust.

Both tiers run through the same order-block/BOS logic every 5 minutes.

## Data & alerting

- **Market data**: Alpaca (free real-time IEX feed on a paper account —
  read-only, no trading).
- **Alerts**: Telegram bot/chat (reused from an existing Polymarket bot).
- **Dedup**: each unique zone (symbol + direction + formation time +
  price) is only alerted once.

## Repo structure

```
config.py           — all tunable parameters (timeframes, ATR multiplier,
                       OTE band, watchlist filters, scan interval)
structure.py         — swing detection, BOS/CHoCH labeling, ATR
order_blocks.py      — order block detection, mitigation, OTE zone math
data_feed.py         — Alpaca historical/snapshot data wrapper
market_universe.py   — S&P 500 + Nasdaq 100 list, movers screener
telegram_alert.py     — alert formatting and sending
main.py               — live scanner loop (reads data, alerts only)
backtest_engine.py    — walk-forward simulation (tap → confirm → win/loss/timeout)
backtest.py            — CLI for backtesting (Alpaca or CSV mode)
plot_ob_zones.py        — renders candlestick chart with zones + OTE bands overlaid
requirements.txt, .env.example, ob-scalp-bot.service — deployment
```

## Known limitations / open items

- **Not yet backtested against real historical data** — only validated
  against synthetic test data so far. Run `backtest.py` against real
  symbols before trusting live alerts.
- **Backtest is single-timeframe** (approximates the 5m/1m confirmation
  steps) — see `backtest_engine.py` docstring. Live signals should be
  equal-or-better quality than backtest numbers suggest.
- **Movers tier** currently only scans the core watchlist's own
  snapshots for movers (cheap, no extra cost) rather than the full
  market — swap in a dedicated screener (Polygon, Finviz) for true
  market-wide mover detection.
- **No market-hours awareness yet** — scanner runs continuously; add an
  Alpaca clock check to skip scanning outside regular trading hours.
- **Risk/reward framing in alerts is informational only** — position
  sizing and actual entry/exit decisions are left to the user.

## What Claude Code should treat as ground truth

The rules above (sections "Core concept" through "Data & alerting") are
the intended behavior. If code and this spec ever disagree, treat this
spec as the source of truth and flag the discrepancy rather than
silently "fixing" the spec to match the code.
