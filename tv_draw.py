"""
Direct TradingView CDP client.

Draws order block signal levels on TradingView Desktop, creates a price
crossing alert, takes a screenshot, and sends it to Telegram — all without
going through Claude Code.

Called by main.py immediately after a signal is detected.
"""

from __future__ import annotations
import base64
import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests
import websocket  # websocket-client

import config

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
CHART_API = "window.TradingViewApi._activeChartWidgetWV.value()"
SCREENSHOT_DIR = Path(r"C:\Users\bpete\tradingview-mcp\screenshots")


# ---------------------------------------------------------------------------
# CDP connection
# ---------------------------------------------------------------------------

class _CDPClient:
    def __init__(self):
        self._ws = None
        self._msg_id = 0

    def connect(self):
        resp = requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json/list", timeout=5)
        targets = resp.json()
        target = (
            next((t for t in targets if t.get("type") == "page" and "tradingview.com/chart" in t.get("url", "")), None)
            or next((t for t in targets if t.get("type") == "page" and "tradingview" in t.get("url", "").lower()), None)
        )
        if not target:
            raise RuntimeError("TradingView chart not found — is TradingView Desktop open?")
        self._ws = websocket.create_connection(
            target["webSocketDebuggerUrl"],
            timeout=15,
            suppress_origin=True,
        )
        self._raw({"method": "Runtime.enable", "params": {}})
        self._raw({"method": "Page.enable", "params": {}})

    def _raw(self, msg: dict) -> dict:
        self._msg_id += 1
        msg["id"] = self._msg_id
        self._ws.send(json.dumps(msg))
        deadline = time.time() + 10
        while time.time() < deadline:
            data = json.loads(self._ws.recv())
            if data.get("id") == self._msg_id:
                return data
        raise TimeoutError("CDP raw command timed out")

    def evaluate(self, expression: str, await_promise: bool = False):
        if not self._ws:
            self.connect()
        self._msg_id += 1
        msg_id = self._msg_id
        self._ws.send(json.dumps({
            "id": msg_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        }))
        timeout = 30 if await_promise else 10
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = json.loads(self._ws.recv())
            if raw.get("id") != msg_id:
                continue
            result = raw.get("result", {})
            if "exceptionDetails" in result:
                exc = result["exceptionDetails"]
                raise RuntimeError(f"JS error: {exc.get('text', str(exc))}")
            return result.get("result", {}).get("value")
        raise TimeoutError(f"CDP evaluate timed out after {timeout}s")

    def screenshot(self, region: str = "chart") -> str:
        bounds = None
        if region == "chart":
            bounds = self.evaluate("""
                (function() {
                    var el = document.querySelector('[data-name="pane-canvas"]')
                        || document.querySelector('[class*="chart-container"]')
                        || document.querySelector('canvas');
                    if (!el) return null;
                    var r = el.getBoundingClientRect();
                    return {x: r.x, y: r.y, width: r.width, height: r.height};
                })()
            """)

        self._msg_id += 1
        msg_id = self._msg_id
        params: dict = {"format": "png"}
        if bounds:
            params["clip"] = {**bounds, "scale": 1}
        self._ws.send(json.dumps({"id": msg_id, "method": "Page.captureScreenshot", "params": params}))

        deadline = time.time() + 15
        while time.time() < deadline:
            raw = json.loads(self._ws.recv())
            if raw.get("id") != msg_id:
                continue
            img_data = raw.get("result", {}).get("data", "")
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            path = SCREENSHOT_DIR / f"tv_signal_{ts}.png"
            path.write_bytes(base64.b64decode(img_data))
            return str(path)
        raise TimeoutError("Screenshot timed out")

    def close(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None


# ---------------------------------------------------------------------------
# TradingView operations
# ---------------------------------------------------------------------------

def _js_str(s: str) -> str:
    return json.dumps(str(s))


def _set_symbol(cdp: _CDPClient, symbol: str) -> None:
    cdp.evaluate(f"""
        (function() {{
            var chart = {CHART_API};
            return new Promise(function(resolve) {{
                chart.setSymbol({_js_str(symbol)}, {{}});
                setTimeout(resolve, 1000);
            }});
        }})()
    """, await_promise=True)


def _draw_shape(cdp: _CDPClient, shape: str, point: dict, point2: dict | None = None,
                overrides: dict | None = None, text: str = "") -> str | None:
    overrides_str = json.dumps(overrides or {})
    text_str = json.dumps(text)
    p1 = f"{{time:{point['time']}, price:{point['price']}}}"

    before = cdp.evaluate(f"{CHART_API}.getAllShapes().map(function(s){{return s.id;}})")

    if point2:
        p2 = f"{{time:{point2['time']}, price:{point2['price']}}}"
        cdp.evaluate(f"""
            {CHART_API}.createMultipointShape(
                [{p1}, {p2}],
                {{shape:{_js_str(shape)}, overrides:{overrides_str}, text:{text_str}}}
            )
        """)
    else:
        cdp.evaluate(f"""
            {CHART_API}.createShape(
                {p1},
                {{shape:{_js_str(shape)}, overrides:{overrides_str}, text:{text_str}}}
            )
        """)

    time.sleep(0.25)
    after = cdp.evaluate(f"{CHART_API}.getAllShapes().map(function(s){{return s.id;}})")
    before_set = set(before or [])
    new_ids = [i for i in (after or []) if i not in before_set]
    return new_ids[0] if new_ids else None


def _create_alert(cdp: _CDPClient, price: float, message: str) -> None:
    cdp.evaluate(f"""
        (function() {{
            try {{
                var ms = {CHART_API}._chartWidget.model().mainSeries();
                var sym = (ms.proSymbol && ms.proSymbol()) || (ms.symbol && ms.symbol());
                var payload = {{
                    conditions: [{{
                        type: "cross", frequency: "on_first_fire",
                        series: [{{type:"barset"}}, {{type:"value", value:{price}}}],
                        resolution: "1"
                    }}],
                    symbol: '={{"symbol":"' + sym + '"}}',
                    resolution: "1",
                    message: {_js_str(message)},
                    sound_file: "alert/fired", sound_duration: 0,
                    popup: true, auto_deactivate: true,
                    email: false, mobile_push: true,
                    web_hook: null, name: null,
                    expiration: new Date(Date.now() + 30*24*3600*1000).toISOString(),
                    active: true, ignore_warnings: true
                }};
                var x = new XMLHttpRequest();
                x.open("POST", "https://pricealerts.tradingview.com/create_alert", false);
                x.withCredentials = true;
                x.setRequestHeader("Content-Type", "text/plain;charset=UTF-8");
                x.send(JSON.stringify({{payload: payload}}));
                return JSON.parse(x.responseText || "{{}}");
            }} catch(e) {{ return {{error: e.message}}; }}
        }})()
    """)


def _set_visible_range(cdp: _CDPClient, from_ts: int, to_ts: int) -> None:
    """Best-effort scroll to show the OB zone. Falls back silently if it fails."""
    try:
        cdp.evaluate(f"""
            (function() {{
                try {{
                    var chart = {CHART_API};
                    var m = chart._chartWidget.model();
                    var ts = m.timeScale();
                    var bars = m.mainSeries().bars();
                    var n = bars.size();
                    if (!n) return;
                    var fromIdx = null, toIdx = null;
                    for (var i = bars.firstIndex(); i <= bars.lastIndex(); i++) {{
                        var v = bars.valueAt(i);
                        if (!v) continue;
                        var t = v[0];
                        if (fromIdx === null && t >= {from_ts}) fromIdx = i;
                        if (t <= {to_ts}) toIdx = i;
                    }}
                    if (fromIdx !== null && toIdx !== null) {{
                        ts.setVisibleRange({{from: fromIdx, to: toIdx + 5}});
                    }}
                }} catch(e) {{}}
            }})()
        """)
        time.sleep(0.5)
    except Exception:
        pass


def _set_right_offset(cdp: _CDPClient, bars: int = 5) -> None:
    """Scroll the chart right so `bars` empty future bars are visible — this
    puts the position-tool stats box inside the viewport."""
    try:
        cdp.evaluate(f"""
            (function() {{
                try {{
                    var m = {CHART_API}._chartWidget.model();
                    m.timeScale().setRightOffset({bars});
                }} catch(e) {{}}
            }})()
        """)
        time.sleep(0.3)
    except Exception:
        pass


def _draw_position_tool(cdp: _CDPClient, direction: str,
                        entry: float, tp: float, sl: float,
                        formed_ts: int, right_ts: int) -> str | None:
    """Draw TradingView's native Long/Short Position tool (shaded profit + risk
    zones with the R/R stats box).  Returns the new shape ID or None."""
    shape = "long_position" if direction == "bullish" else "short_position"
    p1 = f"{{time:{formed_ts},price:{entry}}}"
    p2 = f"{{time:{right_ts},price:{tp}}}"
    p3 = f"{{time:{right_ts},price:{sl}}}"

    before = cdp.evaluate(f"{CHART_API}.getAllShapes().map(function(s){{return s.id;}})")
    cdp.evaluate(f"""
        {CHART_API}.createMultipointShape(
            [{p1},{p2},{p3}],
            {{shape:{_js_str(shape)},overrides:{{}},text:""}}
        )
    """)
    time.sleep(0.3)
    after = cdp.evaluate(f"{CHART_API}.getAllShapes().map(function(s){{return s.id;}})")
    before_set = set(before or [])
    new_ids = [i for i in (after or []) if i not in before_set]
    return new_ids[0] if new_ids else None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def draw_signal(ob, entry: float, sl: float, tp1: float, tp2: float) -> str | None:
    """
    Draws the signal on TradingView, creates a crossing alert, takes a
    screenshot, sends it to Telegram, and returns the screenshot path.
    Returns None if TradingView is not reachable (bot keeps running).
    """
    cdp = _CDPClient()
    try:
        cdp.connect()

        _set_symbol(cdp, ob.symbol)
        time.sleep(1.2)  # let chart load

        now_ts = int(datetime.now(timezone.utc).timestamp())
        try:
            formed_unix = int(ob.formed_at.timestamp())
        except Exception:
            formed_unix = now_ts - 3600

        # OB zone rectangle
        bg = "#1565C0" if ob.direction == "bullish" else "#6A1B9A"
        _draw_shape(cdp, "rectangle",
                    point={"time": formed_unix, "price": ob.zone_high},
                    point2={"time": now_ts, "price": ob.zone_low},
                    overrides={"backgroundColor": bg, "backgroundTransparency": 70,
                                "linecolor": bg, "linewidth": 1},
                    text="OB Zone")

        # Entry / SL / TP lines
        _draw_shape(cdp, "horizontal_line",
                    point={"time": now_ts, "price": entry},
                    overrides={"linecolor": "#FFFFFF", "linewidth": 2, "linestyle": 0},
                    text="Entry")

        _draw_shape(cdp, "horizontal_line",
                    point={"time": now_ts, "price": sl},
                    overrides={"linecolor": "#F44336", "linewidth": 2, "linestyle": 0},
                    text="SL")

        _draw_shape(cdp, "horizontal_line",
                    point={"time": now_ts, "price": tp1},
                    overrides={"linecolor": "#4CAF50", "linewidth": 2, "linestyle": 0},
                    text="TP1")

        _draw_shape(cdp, "horizontal_line",
                    point={"time": now_ts, "price": tp2},
                    overrides={"linecolor": "#A5D6A7", "linewidth": 2, "linestyle": 1},
                    text="TP2")

        # Profit zone (green) and risk zone (red) rectangles — same visual as
        # the Long/Short Position tool but with exact user-defined prices.
        right_ts = now_ts + 5 * 900  # extend 5 bars into the future
        _draw_shape(cdp, "rectangle",
                    point={"time": formed_unix, "price": tp1},
                    point2={"time": right_ts, "price": entry},
                    overrides={"backgroundColor": "#4CAF50", "backgroundTransparency": 75,
                               "linecolor": "#4CAF50", "linewidth": 1},
                    text="")
        _draw_shape(cdp, "rectangle",
                    point={"time": formed_unix, "price": entry},
                    point2={"time": right_ts, "price": sl},
                    overrides={"backgroundColor": "#F44336", "backgroundTransparency": 75,
                               "linecolor": "#F44336", "linewidth": 1},
                    text="")

        # TradingView crossing alert at entry
        arrow = "BUY" if ob.direction == "bullish" else "SELL"
        alert_msg = (f"OB Entry triggered: {ob.symbol} {arrow} — "
                     f"Entry {entry:.2f} | SL {sl:.2f} | TP1 {tp1:.2f}")
        _create_alert(cdp, entry, alert_msg)

        # Zoom to signal window
        _set_visible_range(cdp, formed_unix - 7200, now_ts + 1800)
        _set_right_offset(cdp, bars=6)
        time.sleep(0.8)  # let chart render

        # Screenshot → Telegram
        screenshot_path = cdp.screenshot(region="chart")
        _send_photo(screenshot_path, ob.symbol, ob.direction)
        return screenshot_path

    except Exception:
        print(f"[tv_draw] Failed for {ob.symbol}:")
        traceback.print_exc()
        return None
    finally:
        cdp.close()


def _send_photo(image_path: str, symbol: str, direction: str) -> None:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    arrow = "BUY" if direction == "bullish" else "SELL"
    caption = f"{'🟢' if direction == 'bullish' else '🔴'} *{symbol}* {arrow} — OB levels on chart"
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(image_path, "rb") as f:
            requests.post(
                url,
                data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "Markdown"},
                files={"photo": f},
                timeout=15,
            ).raise_for_status()
    except Exception as e:
        print(f"[tv_draw] Telegram photo failed: {e}")
