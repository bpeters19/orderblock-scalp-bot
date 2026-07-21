# Order Block Scanner / Alert Bot

Scans stocks for order-block setups (mechanical SMC/order-block trading —
the last opposing candle before a structure-breaking move) and sends you
a Telegram alert when a valid, unmitigated zone is tapped with lower-timeframe
confirmation. **It only reads market data and sends notifications — it never
places trades.** You decide whether to take the alert.

## How the logic works

- **Structure (`structure.py`)** — detects swing highs/lows, then labels
  each bar as a **BOS** (Break of Structure, continuation) or **CHoCH**
  (Change of Character, likely reversal) using ATR to filter weak breaks.
- **Order blocks (`order_blocks.py`)** — for every BOS/CHoCH, finds the
  last opposing candle before it (the institutional footprint), draws
  the zone, computes the **OTE (Optimal Trade Entry)** band from the
  impulse leg (62%–79% retracement), and tracks mitigation (has price
  already closed through it) and tap status (has price already touched it).
- **Multi-timeframe model:**
  - `15Min` → find the order block zone (config: `STRUCTURE_TF`)
  - `5Min` → confirm price has actually traded back into the zone (`TAP_TF`)
  - `1Min` → require a mini BOS/CHoCH in the same direction before
    alerting (`CONFIRM_TF`) — this is the actual entry trigger
- **Two scan tiers:**
  - **Core**: S&P 500 + Nasdaq 100 (stable, liquid, cleaner structure)
  - **Movers**: top gainers/losers filtered by price/volume (more volatile,
    tag it in the alert so you can size down)

## Setup

1. **Create a free Alpaca account** (paper trading is fine — this bot never
   trades) and generate an API key/secret.
2. **Reuse your existing Telegram bot** from the Polymarket project, or
   create a new one via @BotFather, then grab your chat ID.
3. Copy `.env.example` to `.env` and fill in your keys.
4. Install dependencies:
   ```bash
   pip install -r requirements.txt --break-system-packages
   ```
5. Test it locally:
   ```bash
   export $(cat .env | xargs)
   python3 main.py
   ```

## Deploying on your droplet (same pattern as the Polymarket bot)

```bash
scp -r ob_scalp_bot root@your-droplet-ip:/root/
ssh root@your-droplet-ip
cd /root/ob_scalp_bot
pip install -r requirements.txt --break-system-packages
cp .env.example .env   # then edit with real keys
cp ob-scalp-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ob-scalp-bot
systemctl start ob-scalp-bot
journalctl -u ob-scalp-bot -f   # tail logs
```

## Tuning knobs (all in `config.py`)

| Setting | What it does |
|---|---|
| `STRUCTURE_TF` / `TAP_TF` / `CONFIRM_TF` | The three timeframes in the model |
| `MIN_DISPLACEMENT_ATR_MULT` | Higher = fewer, higher-conviction order blocks |
| `OTE_FIB_LOW` / `OTE_FIB_HIGH` | The retracement band considered a quality entry |
| `MAX_OB_AGE_BARS` | Ignore stale zones older than this many structure-TF bars |
| `MOVERS_MIN_PRICE` / `MOVERS_MIN_AVG_VOLUME` / `MOVERS_MIN_PCT_CHANGE` | Filters for tier-2 movers scan |
| `SCAN_INTERVAL_SECONDS` | How often the loop runs |

## Known limitations / next steps

- **Data feed**: Alpaca's free IEX feed is real-time but is a partial
  view of the tape (not full SIP consolidated data). Fine for structure/OB
  detection; if you later want tick-perfect fills data, upgrade to Alpaca's
  paid SIP feed or Polygon.io.
- **Movers tier** currently derives "movers" from snapshots of the core
  list itself (cheap, no extra API cost). For a true market-wide mover
  scan (catching small/mid caps outside S&P/Nasdaq-100), swap in a
  dedicated screener endpoint (Polygon `/v2/snapshot/locale/us/markets/stocks/gainers`,
  or Finviz) in `market_universe.get_movers()`.
- **Market hours**: the loop currently runs continuously; add an Alpaca
  clock check (`GET /v2/clock`) if you want it to sleep outside regular
  trading hours instead of scanning (harmlessly) around the clock.
- **Backtesting**: this rule set hasn't been backtested yet. Before
  trusting live alerts, it's worth running `find_order_blocks` over
  historical bars for your watchlist and manually reviewing a sample of
  the zones it would have flagged.

## Backtesting

Two modes — pull real history from Alpaca, or test offline against a CSV:

```bash
# Alpaca mode (uses your .env keys)
python3 backtest.py --symbols AAPL,MSFT,NVDA --days 90 --timeframe 15Min

# Offline CSV mode (columns: timestamp,open,high,low,close,volume)
python3 backtest.py --csv path/to/AAPL_15min.csv --symbol AAPL
```

**Important caveat**: this backtest is a SIMPLIFIED single-timeframe
simulation (see `backtest_engine.py` docstring). It doesn't replicate the
live bot's full 15m-zone / 5m-tap / 1m-confirm model — it approximates the
lower-timeframe confirmation with "does the very next bar close in the
trade direction." That means:
- Live alerts should be equal or higher quality than what this shows
  (fewer, more selective signals), since the real confirmation step is
  stricter.
- Treat these numbers as a sanity check on the core order-block/OTE logic
  (are zones being drawn sensibly, is win rate/expectancy in a reasonable
  ballpark), not as a guarantee of live performance.

Results print a per-symbol and overall summary (win rate, average R
multiple, expectancy in R) and write every simulated trade to
`backtest_results.csv` for manual review.

## Visualizing order block zones

Plots candles with detected order-block zones (green = bullish, red =
bearish) and their OTE bands overlaid, so you can eyeball whether the
logic is flagging sensible zones:

```bash
# Alpaca mode
python3 plot_ob_zones.py --symbol AAPL --timeframe 15Min --days 30

# Offline CSV mode
python3 plot_ob_zones.py --csv path/to/AAPL_15min.csv --symbol AAPL
```

Saves a PNG (`<symbol>_ob_zones.png` by default) rather than opening an
interactive window, since this is meant to run headless on a droplet too.

### Overlaying projected long/short trades

Add `--show-trades` to also draw the simulated entry, stop-loss, and
take-profit levels for each zone (same simulation logic as
`backtest.py`) — a long shows as an upward triangle, short as a downward
triangle, with the outcome (win/loss/timeout) and R-multiple labeled:

```bash
python3 plot_ob_zones.py --symbol AAPL --days 30 --show-trades
```
