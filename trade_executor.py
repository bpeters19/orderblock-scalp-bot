"""
trade_executor.py — optional auto-execution layer for the Order Block scanner.

Submits a bracket limit order (limit entry + stop-loss + take-profit) to
Alpaca's Trading API when a valid order-block setup is confirmed.

Safety model (all enforced before any order is submitted):
  1. AUTO_EXECUTE_ENABLED must be True in config.
  2. TRADING_MODE must be "paper" unless LIVE_TRADING_CONFIRMED is also
     explicitly True in config — two separate flags required for live.
  3. Kill switch: if .trading_halted exists on disk, all execution is
     skipped (alerts still fire).
  4. MAX_CONCURRENT_POSITIONS: checked live via Alpaca — skips if at cap.
  5. MAX_DAILY_TRADES: hard cap on new positions opened today.

All submissions, skips, and failures are written to TRADE_LOG_PATH
(one JSON record per line) in addition to stdout.

Called from main.py's scan_symbol() after a valid alert fires.
Returns a dict with keys: {"status", "reason", "order_id"}.
  status: "submitted" | "skipped" | "error"
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest

import config


# ---------------------------------------------------------------------------
# Internal state — daily trade counter (reset at midnight automatically)
# ---------------------------------------------------------------------------

@dataclass
class _DailyCounter:
    day: date
    count: int

_counter: _DailyCounter | None = None


def _daily_count() -> int:
    global _counter
    today = datetime.now(timezone.utc).date()
    if _counter is None or _counter.day != today:
        _counter = _DailyCounter(day=today, count=0)
    return _counter.count


def _increment_daily_count() -> None:
    global _counter
    today = datetime.now(timezone.utc).date()
    if _counter is None or _counter.day != today:
        _counter = _DailyCounter(day=today, count=0)
    _counter.count += 1


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(record: dict) -> None:
    """Append a JSON record to the persistent trade log and echo to stdout."""
    record.setdefault("ts", datetime.now(timezone.utc).isoformat())
    line = json.dumps(record)
    print(f"[trade_executor] {line}")
    try:
        with open(config.TRADE_LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception as exc:
        print(f"[trade_executor] WARNING: could not write trade log: {exc}")


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------

def _check_safety(client: TradingClient) -> tuple[bool, str]:
    """
    Run all pre-submission safety checks in order.
    Returns (ok, reason). If ok is False, reason explains why.
    """
    # 1. Kill switch
    if os.path.exists(config.KILL_SWITCH_PATH):
        return False, "kill_switch_active"

    # 2. Live trading double-confirmation
    if config.TRADING_MODE == "live" and not config.LIVE_TRADING_CONFIRMED:
        return False, "live_trading_not_confirmed"

    # 3. Daily trade cap
    if _daily_count() >= config.MAX_DAILY_TRADES:
        return False, f"daily_cap_reached ({_daily_count()}/{config.MAX_DAILY_TRADES})"

    # 4. Concurrent position cap (live check)
    try:
        positions = client.get_all_positions()
        if len(positions) >= config.MAX_CONCURRENT_POSITIONS:
            return False, f"position_cap_reached ({len(positions)}/{config.MAX_CONCURRENT_POSITIONS})"
    except Exception as exc:
        return False, f"position_check_failed: {exc}"

    return True, ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def submit_bracket_order(
    ob,
    entry: float,
    sl: float,
    tp2: float,
    shares: int,
) -> dict:
    """
    Submit a bracket limit order to Alpaca.

    Parameters
    ----------
    ob     : OrderBlock — the detected order block (symbol, direction, …)
    entry  : float — limit entry price (OTE mid)
    sl     : float — stop-loss price
    tp2    : float — take-profit price (3R target)
    shares : int   — position size (pre-computed by calc_ob_levels caller)

    Returns
    -------
    dict with keys: status ("submitted" | "skipped" | "error"),
                    reason (str), order_id (str | None)
    """
    base_record = {
        "symbol": ob.symbol,
        "direction": ob.direction,
        "shares": shares,
        "entry": entry,
        "sl": sl,
        "tp2": tp2,
    }

    # --- master switch ---
    if not config.AUTO_EXECUTE_ENABLED:
        return {"status": "skipped", "reason": "auto_execute_disabled", "order_id": None}

    # --- refuse to use live trading without both flags set ---
    if config.TRADING_MODE == "live" and not config.LIVE_TRADING_CONFIRMED:
        _log({**base_record, "status": "skipped", "reason": "live_trading_not_confirmed"})
        return {"status": "skipped", "reason": "live_trading_not_confirmed", "order_id": None}

    paper = config.TRADING_MODE != "live"
    client = TradingClient(
        api_key=config.ALPACA_API_KEY,
        secret_key=config.ALPACA_SECRET_KEY,
        paper=paper,
    )

    # --- safety checks ---
    ok, reason = _check_safety(client)
    if not ok:
        _log({**base_record, "status": "skipped", "reason": reason})
        return {"status": "skipped", "reason": reason, "order_id": None}

    if shares <= 0:
        _log({**base_record, "status": "skipped", "reason": "zero_shares"})
        return {"status": "skipped", "reason": "zero_shares", "order_id": None}

    # --- build the bracket order ---
    side = OrderSide.BUY if ob.direction == "bullish" else OrderSide.SELL

    # Round prices to 2 decimal places — Alpaca rejects more precision on
    # most US equities (sub-penny rule).
    limit_price = round(entry, 2)
    stop_price = round(sl, 2)
    take_profit_price = round(tp2, 2)

    order_req = LimitOrderRequest(
        symbol=ob.symbol,
        qty=shares,
        side=side,
        time_in_force=TimeInForce.DAY,
        limit_price=limit_price,
        order_class=OrderClass.BRACKET,
        stop_loss={"stop_price": stop_price},
        take_profit={"limit_price": take_profit_price},
    )

    # --- submit ---
    try:
        order = client.submit_order(order_req)
        _increment_daily_count()
        record = {
            **base_record,
            "status": "submitted",
            "order_id": str(order.id),
            "limit_price": limit_price,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "alpaca_status": str(order.status),
            "trading_mode": config.TRADING_MODE,
        }
        _log(record)
        return {"status": "submitted", "reason": "", "order_id": str(order.id)}

    except Exception as exc:
        record = {**base_record, "status": "error", "reason": str(exc)}
        _log(record)
        return {"status": "error", "reason": str(exc), "order_id": None}
