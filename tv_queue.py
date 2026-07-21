"""
TradingView signal queue.

When the bot fires an alert it appends a signal here. A separate process
(the Claude Code /loop) reads pending signals and draws the levels on the
live TradingView chart via MCP.
"""

from __future__ import annotations
import json
import os
from datetime import datetime, timezone

_QUEUE_PATH = os.path.join(os.path.dirname(__file__), ".tv_queue.json")


def push_signal(
    ob,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
) -> None:
    formed_ts = ob.formed_at
    # Convert pandas Timestamp → unix int if needed
    try:
        formed_unix = int(formed_ts.timestamp())
    except Exception:
        formed_unix = int(datetime.now(timezone.utc).timestamp())

    signal = {
        "symbol": ob.symbol,
        "direction": ob.direction,
        "entry": round(entry, 4),
        "sl": round(sl, 4),
        "tp1": round(tp1, 4),
        "tp2": round(tp2, 4),
        "ob_zone_low": round(ob.zone_low, 4),
        "ob_zone_high": round(ob.zone_high, 4),
        "formed_unix": formed_unix,
        "fired_at": datetime.now(timezone.utc).isoformat(),
        "drawn": False,
    }
    queue = _load()
    queue.append(signal)
    _save(queue[-500:])


def pop_pending() -> list[dict]:
    """Return all undrawn signals and mark them as drawn in the queue file."""
    queue = _load()
    pending = [s for s in queue if not s.get("drawn")]
    for s in pending:
        s["drawn"] = True
    _save(queue)
    return pending


def _load() -> list:
    if os.path.exists(_QUEUE_PATH):
        try:
            with open(_QUEUE_PATH) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save(queue: list) -> None:
    with open(_QUEUE_PATH, "w") as f:
        json.dump(queue, f, indent=2)
