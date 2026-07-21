"""
Configuration for the Order Block Scanner/Alert Bot.
Fill in your keys via environment variables (recommended) or edit the
defaults below directly. Never commit real keys to git.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Alpaca API credentials (paper trading keys are fine — we only READ data)
# ---------------------------------------------------------------------------
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = True  # Keep True — this bot only reads market data, never trades

# ---------------------------------------------------------------------------
# Telegram (reuse the same bot/chat you already set up for the Polymarket bot)
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------------------
# Timeframes for the multi-timeframe order block model
#   structure_tf -> where the order block zone itself is identified
#   tap_tf       -> where we watch for price to retrace into the zone
#   confirm_tf   -> where we require a mini BOS/CHoCH to trigger the alert
# ---------------------------------------------------------------------------
STRUCTURE_TF = "15Min"
TAP_TF = "5Min"
CONFIRM_TF = "1Min"

# ---------------------------------------------------------------------------
# Order block validity rules
# ---------------------------------------------------------------------------
MIN_DISPLACEMENT_ATR_MULT = 1.5   # break-of-structure leg must be >= this * ATR
OTE_FIB_LOW = 0.62                # Optimal Trade Entry zone (retracement %)
OTE_FIB_HIGH = 0.79
SWING_LOOKBACK = 3                # bars on each side to confirm a swing high/low
MAX_OB_AGE_BARS = 40              # ignore order blocks older than this (structure_tf bars)

# ---------------------------------------------------------------------------
# Scanning tiers
# ---------------------------------------------------------------------------
SCAN_INTERVAL_SECONDS = 300        # how often the scanner loop runs (5 min)

# Tier 1: stable, liquid core watchlist (edit/replace with a live constituents
# pull — see market_universe.py for a helper). Small starter set shown here.
CORE_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD",
    "SPY", "QQQ", "AVGO", "NFLX", "JPM", "V", "UNH", "COST",
]

# Tier 2: momentum/movers filters
MOVERS_MIN_PRICE = 5.0
MOVERS_MIN_AVG_VOLUME = 1_000_000
MOVERS_TOP_N = 30          # how many top gainers/losers to pull each scan
MOVERS_MIN_PCT_CHANGE = 3.0  # only consider movers up/down at least this %

# ---------------------------------------------------------------------------
# Risk framing shown in the alert (informational only — you decide sizing)
# ---------------------------------------------------------------------------
DEFAULT_RISK_REWARD_TARGET = 2.0  # used only to display a suggested TP distance

# ---------------------------------------------------------------------------
# Position sizing (informational only — displayed in alerts/charts, not
# used to place any real trades). Adjust ACCOUNT_EQUITY to match your
# actual account size for the dollar figures to mean anything.
# ---------------------------------------------------------------------------
ACCOUNT_EQUITY = float(os.environ.get("ACCOUNT_EQUITY", 100_000))  # paper acct default
RISK_PER_TRADE_PCT = 1.0  # % of account equity risked per trade idea
