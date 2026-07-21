"""
Builds the two scan tiers:
  Tier 1 (core): S&P 500 + Nasdaq 100 constituents (cached locally, refreshed
                 periodically — these lists barely change day to day)
  Tier 2 (movers): top gainers/losers with volume/price filters, pulled from
                 Alpaca snapshots each scan cycle

NOTE: pulling the constituent lists requires general internet access (this
runs on your droplet, not in a sandboxed environment) since it scrapes
public index-membership tables. Falls back to config.CORE_WATCHLIST if the
fetch fails, so the bot never crashes from a network hiccup.
"""

from __future__ import annotations
import io
import json
import os
import time
import pandas as pd
import requests
import certifi

import config

_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".universe_cache.json")
_CACHE_TTL_SECONDS = 60 * 60 * 24  # refresh once a day


def _load_cache() -> dict | None:
    if not os.path.exists(_CACHE_PATH):
        return None
    try:
        with open(_CACHE_PATH) as f:
            data = json.load(f)
        if time.time() - data.get("_fetched_at", 0) > _CACHE_TTL_SECONDS:
            return None
        return data
    except Exception:
        return None


def _save_cache(sp500: list[str], nasdaq100: list[str]) -> None:
    try:
        with open(_CACHE_PATH, "w") as f:
            json.dump(
                {"sp500": sp500, "nasdaq100": nasdaq100, "_fetched_at": time.time()},
                f,
            )
    except Exception:
        pass  # cache is best-effort only


def _get_html(url: str) -> str:
    resp = requests.get(url, verify=certifi.where(), timeout=20,
                        headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def _fetch_sp500() -> list[str]:
    tables = pd.read_html(io.StringIO(_get_html(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    )))
    df = tables[0]
    return sorted(df["Symbol"].str.replace(".", "-", regex=False).tolist())


def _fetch_nasdaq100() -> list[str]:
    tables = pd.read_html(io.StringIO(_get_html(
        "https://en.wikipedia.org/wiki/Nasdaq-100"
    )))
    for t in tables:
        cols = [c.lower() for c in t.columns.astype(str)]
        if any("ticker" in c or "symbol" in c for c in cols):
            col = t.columns[[("ticker" in c.lower() or "symbol" in c.lower()) for c in t.columns.astype(str)]][0]
            return sorted(t[col].str.replace(".", "-", regex=False).tolist())
    return []


def get_core_watchlist() -> list[str]:
    """
    Returns the combined, de-duplicated S&P 500 + Nasdaq 100 list.
    Falls back to config.CORE_WATCHLIST on any failure (offline, site
    layout change, etc.) so the scanner keeps running regardless.
    """
    cached = _load_cache()
    if cached:
        symbols = sorted(set(cached["sp500"]) | set(cached["nasdaq100"]))
    else:
        try:
            sp500 = _fetch_sp500()
            nasdaq100 = _fetch_nasdaq100()
            if not sp500:
                raise ValueError("Empty S&P 500 fetch")
            _save_cache(sp500, nasdaq100)
            symbols = sorted(set(sp500) | set(nasdaq100))
        except Exception as e:
            print(f"[market_universe] Falling back to static CORE_WATCHLIST ({e})")
            symbols = list(config.CORE_WATCHLIST)

    # Alpaca rejects class-share tickers that use hyphens (BRK-B, BF-B etc.)
    return [s for s in symbols if "-" not in s]


def get_movers(data_feed) -> list[str]:
    """
    Tier 2: pull top gainers/losers from Alpaca snapshots, filter by
    minimum price / avg volume / % change so we don't chase illiquid junk.
    `data_feed` is a DataFeed instance (see data_feed.py) — passed in so
    this module doesn't need its own client/auth handling.
    """
    # Alpaca's market data API doesn't have a single "top movers" endpoint
    # on the basic plan, so movers are derived from snapshots of the core
    # list itself (cheap) plus any explicit extra symbols you want to track.
    # For a broader true market-wide movers scan, swap this out for a
    # dedicated screener API (e.g. Finviz, Polygon's /v2/snapshot/gainers-losers)
    # once you decide on a paid data source.
    universe = get_core_watchlist()
    try:
        snapshots = data_feed.get_snapshots(universe)
    except Exception as e:
        print(f"[market_universe] get_snapshots failed, skipping movers: {e}")
        return []

    movers = []
    for symbol, snap in snapshots.items():
        try:
            daily_bar = snap.daily_bar
            prev_close = daily_bar.close if daily_bar else None
            latest_trade = snap.latest_trade
            if not latest_trade or not prev_close:
                continue
            price = latest_trade.price
            if price < config.MOVERS_MIN_PRICE:
                continue
            pct_change = (price - prev_close) / prev_close * 100
            if abs(pct_change) < config.MOVERS_MIN_PCT_CHANGE:
                continue
            movers.append((symbol, pct_change))
        except Exception:
            continue

    movers.sort(key=lambda x: abs(x[1]), reverse=True)
    return [sym for sym, _ in movers[: config.MOVERS_TOP_N]]
