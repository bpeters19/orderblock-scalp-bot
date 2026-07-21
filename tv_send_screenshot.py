"""
Called by the Claude Code TV queue loop after drawing signal levels.
Usage: python tv_send_screenshot.py <screenshot_path> <symbol> <direction>
Sends the chart screenshot to Telegram as a photo.
"""

import sys
import requests
from pathlib import Path
import config

def send_chart_photo(image_path: str, symbol: str, direction: str) -> None:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("[tv_send_screenshot] Telegram not configured.")
        return

    arrow = "🟢 BUY" if direction == "bullish" else "🔴 SELL"
    caption = f"{arrow} *{symbol}* — chart with entry / SL / TP levels"

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(image_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "Markdown"},
            files={"photo": f},
            timeout=15,
        )
    resp.raise_for_status()
    print(f"[tv_send_screenshot] Photo sent for {symbol}.")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python tv_send_screenshot.py <path> <symbol> <direction>")
        sys.exit(1)
    send_chart_photo(sys.argv[1], sys.argv[2], sys.argv[3])
