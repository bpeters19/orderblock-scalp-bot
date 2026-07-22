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
# Alpaca API credentials
# ---------------------------------------------------------------------------
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = True  # Always True unless you explicitly understand the risk

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
MIN_RISK_DOLLARS = 0.75   # skip signals where SL distance < this (filters low-price noise)
SYMBOL_COOLDOWN_MINUTES = 60  # don't re-alert the same symbol within this window

# ---------------------------------------------------------------------------
# Auto-execution (DISABLED by default — read every comment before enabling)
# ---------------------------------------------------------------------------

# Master switch. When False, the bot alerts only — no orders are ever placed.
AUTO_EXECUTE_ENABLED = os.environ.get("AUTO_EXECUTE_ENABLED", "false").lower() == "true"

# "paper" = Alpaca paper account (safe to test).
# "live"  = real money. Requires LIVE_TRADING_CONFIRMED = True as a second
#           explicit opt-in. The bot refuses to trade live unless BOTH flags
#           are set — flipping one alone does nothing.
TRADING_MODE = os.environ.get("TRADING_MODE", "paper").lower()

# Second confirmation required for live trading. Must be explicitly set to
# True in code (not via env var) to prevent accidental live execution.
# DO NOT change this to True unless you fully understand the consequences.
LIVE_TRADING_CONFIRMED: bool = False

# Hard cap on open positions at any one time (checked live via Alpaca API).
MAX_CONCURRENT_POSITIONS = int(os.environ.get("MAX_CONCURRENT_POSITIONS", 5))

# Hard cap on NEW positions opened in a single trading day.
MAX_DAILY_TRADES = int(os.environ.get("MAX_DAILY_TRADES", 10))

# Kill switch: if this file exists the executor skips all order submissions.
# Telegram alerts still fire. Create the file to halt; delete it to resume.
#   touch .trading_halted     # halt
#   rm .trading_halted         # resume
KILL_SWITCH_PATH = os.path.join(os.path.dirname(__file__), ".trading_halted")

# Persistent trade log (one JSON record per line).
TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trade_log.jsonl")
