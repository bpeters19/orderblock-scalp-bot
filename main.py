"""
Order Block Scanner / Alert Bot — main loop.

Every SCAN_INTERVAL_SECONDS:
  1. Build the two watchlist tiers (core S&P500+Nasdaq100, and movers)
  2. For each symbol, pull STRUCTURE_TF bars and find unmitigated order
     blocks that overlap the OTE band
  3. For any such order block, pull TAP_TF bars — has price actually
     traded into the zone recently?
  4. If tapped, pull CONFIRM_TF bars and look for a mini BOS/CHoCH in the
     same direction — this is the actual entry trigger
  5. Fire a Telegram alert (deduped so you don't get spammed for the same
     zone every single cycle)

This bot ONLY reads data and sends notifications. It never places trades.
"""

from __future__ import annotations
import json
import os
import time
import traceback
from datetime import datetime, timezone

import config
from data_feed import DataFeed
from order_blocks import find_order_blocks, OrderBlock
from structure import label_structure
from market_universe import get_core_watchlist, get_movers
from telegram_alert import send_alert, format_ob_alert, calc_ob_levels
import tv_queue
import tv_draw

_SEEN_PATH = os.path.join(os.path.dirname(__file__), ".seen_alerts.json")
_COOLDOWN_PATH = os.path.join(os.path.dirname(__file__), ".symbol_cooldown.json")


def _load_seen() -> set[str]:
    if os.path.exists(_SEEN_PATH):
        try:
            with open(_SEEN_PATH) as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def _save_seen(seen: set[str]) -> None:
    try:
        with open(_SEEN_PATH, "w") as f:
            json.dump(sorted(seen)[-2000:], f)  # cap growth; sorted for stable output
    except Exception:
        pass


def _load_cooldown() -> dict[str, float]:
    """Returns {symbol: expiry_unix_ts}. Prunes expired entries on load."""
    if os.path.exists(_COOLDOWN_PATH):
        try:
            with open(_COOLDOWN_PATH) as f:
                raw: dict = json.load(f)
            now = time.time()
            return {k: v for k, v in raw.items() if v > now}
        except Exception:
            return {}
    return {}


def _save_cooldown(cooldown: dict[str, float]) -> None:
    try:
        now = time.time()
        active = {k: v for k, v in cooldown.items() if v > now}
        with open(_COOLDOWN_PATH, "w") as f:
            json.dump(active, f)
    except Exception:
        pass


def _ob_key(ob: OrderBlock) -> str:
    # Use integer Unix timestamp so the key is timezone-representation-agnostic
    try:
        formed_ts = int(ob.formed_at.timestamp())
    except Exception:
        formed_ts = str(ob.formed_at)
    return f"{ob.symbol}|{ob.direction}|{formed_ts}|{ob.zone_low:.4f}"


def _check_confirmation(feed: DataFeed, symbol: str, direction: str) -> str | None:
    """
    Looks at the CONFIRM_TF for a recent BOS/CHoCH in the same direction
    as the order block — this is the lower-timeframe trigger that turns
    a "zone worth watching" into an actual entry alert.
    """
    bars = feed.get_bars(symbol, config.CONFIRM_TF, limit=60)
    if bars.empty or len(bars) < 10:
        return None
    labeled = label_structure(bars, lookback=config.SWING_LOOKBACK)
    recent = labeled.iloc[-3:]  # only count very fresh confirmation
    for _, row in recent.iterrows():
        if row["event"] in ("BOS", "CHoCH"):
            want_trend = "up" if direction == "bullish" else "down"
            if row["trend"] == want_trend:
                return row["event"]
    return None


def _price_in_zone_recently(feed: DataFeed, symbol: str, ob: OrderBlock) -> bool:
    bars = feed.get_bars(symbol, config.TAP_TF, limit=20)
    if bars.empty:
        return False
    recent = bars.iloc[-5:]
    return recent.apply(
        lambda r: ob.contains(r["low"]) or ob.contains(r["high"]) or
        (r["low"] <= ob.zone_high and r["high"] >= ob.zone_low),
        axis=1,
    ).any()


def scan_symbol(
    feed: DataFeed, symbol: str, tier: str,
    seen: set[str], cooldown: dict[str, float],
) -> None:
    try:
        # Per-symbol cooldown: skip entirely if we alerted this symbol recently
        cooldown_exp = cooldown.get(symbol, 0)
        if time.time() < cooldown_exp:
            return

        structure_bars = feed.get_bars(symbol, config.STRUCTURE_TF, limit=150)
        if structure_bars.empty or len(structure_bars) < 20:
            return

        blocks = find_order_blocks(
            structure_bars,
            symbol=symbol,
            swing_lookback=config.SWING_LOOKBACK,
            min_displacement_atr_mult=config.MIN_DISPLACEMENT_ATR_MULT,
            max_age_bars=config.MAX_OB_AGE_BARS,
        )

        for ob in blocks:
            if not ob.overlaps_ote:
                continue
            key = _ob_key(ob)
            if key in seen:
                continue
            if not _price_in_zone_recently(feed, symbol, ob):
                continue

            confirm_event = _check_confirmation(feed, symbol, ob.direction)
            if not confirm_event:
                continue

            lvl = calc_ob_levels(ob)
            if lvl["risk"] < config.MIN_RISK_DOLLARS:
                continue  # SL too tight — not worth the commission/spread

            msg = format_ob_alert(ob, tier=tier, confirm_tf_event=confirm_event)
            send_alert(msg)
            tv_queue.push_signal(ob, lvl["entry"], lvl["sl"], lvl["tp1"], lvl["tp2"])
            tv_draw.draw_signal(ob, lvl["entry"], lvl["sl"], lvl["tp1"], lvl["tp2"])
            seen.add(key)
            cooldown[symbol] = time.time() + config.SYMBOL_COOLDOWN_MINUTES * 60
            break  # one alert per symbol per cooldown window — stop after first hit

    except Exception:
        print(f"[scan_symbol] Error scanning {symbol}:")
        traceback.print_exc()


def run_once(feed: DataFeed, seen: set[str], cooldown: dict[str, float]) -> None:
    core = get_core_watchlist()
    movers = get_movers(feed)

    print(
        f"[{datetime.now(timezone.utc).isoformat()}] Scanning "
        f"{len(core)} core + {len(movers)} mover symbols..."
    )

    for symbol in core:
        scan_symbol(feed, symbol, tier="core", seen=seen, cooldown=cooldown)
        time.sleep(0.4)  # stay under Alpaca free-tier rate limit (200 req/min)

    for symbol in movers:
        if symbol in core:
            continue  # already scanned in core tier
        scan_symbol(feed, symbol, tier="mover", seen=seen, cooldown=cooldown)
        time.sleep(0.15)

    _save_seen(seen)
    _save_cooldown(cooldown)


_LOCK_PATH = os.path.join(os.path.dirname(__file__), ".bot.lock")
_STARTUP_TS_PATH = os.path.join(os.path.dirname(__file__), ".bot_started_at")


def _acquire_lock() -> bool:
    """Returns True if this is the only running instance, False otherwise."""
    if os.path.exists(_LOCK_PATH):
        try:
            with open(_LOCK_PATH) as f:
                pid = int(f.read().strip())
            # Check if that PID is still alive
            import signal
            os.kill(pid, 0)
            return False  # process exists — another instance is running
        except (ValueError, OSError, SystemError):
            pass  # stale lock — process is gone (SystemError on Windows)
    with open(_LOCK_PATH, "w") as f:
        f.write(str(os.getpid()))
    return True


_STARTUP_COOLDOWN_SECONDS = 600  # 10 minutes


def _send_startup_alert() -> None:
    """Send startup message only if we haven't sent one recently."""
    now = time.time()
    try:
        if os.path.exists(_STARTUP_TS_PATH):
            last = float(open(_STARTUP_TS_PATH).read().strip())
            if now - last < _STARTUP_COOLDOWN_SECONDS:
                print(f"[main] Startup alert suppressed (last sent {int(now - last)}s ago)")
                return
    except Exception:
        pass
    send_alert("✅ Order Block scanner started.")
    try:
        with open(_STARTUP_TS_PATH, "w") as f:
            f.write(str(now))
    except Exception:
        pass


def main() -> None:
    if not _acquire_lock():
        print("[main] Another instance is already running. Exiting.")
        return

    try:
        feed = DataFeed()
        seen = _load_seen()
        cooldown = _load_cooldown()

        # Only send startup alert if we haven't sent one in the last 10 minutes
        # (prevents spam when the bot is restarted rapidly during development)
        _send_startup_alert()

        while True:
            try:
                run_once(feed, seen, cooldown)
            except Exception:
                print("[main] Error in scan cycle:")
                traceback.print_exc()
            time.sleep(config.SCAN_INTERVAL_SECONDS)
    finally:
        try:
            os.remove(_LOCK_PATH)
        except OSError:
            pass


_LOG_PATH = os.path.join(os.path.dirname(__file__), "bot.log")


def _redirect_output() -> None:
    """Redirect stdout/stderr to bot.log so the process is self-contained
    when launched detached (no inherited file handles from parent)."""
    import sys
    log = open(_LOG_PATH, "a", buffering=1)
    sys.stdout = log
    sys.stderr = log


if __name__ == "__main__":
    _redirect_output()
    main()
