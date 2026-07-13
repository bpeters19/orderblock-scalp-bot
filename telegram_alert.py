"""
Sends alerts to Telegram — reuse the same bot/chat you already set up for
the Polymarket copy-trading bot so everything lands in one place.
"""

from __future__ import annotations
import requests
import config


def send_alert(text: str) -> None:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("[telegram_alert] Not configured — printing instead:\n" + text)
        return

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[telegram_alert] Failed to send: {e}")


def format_ob_alert(ob, tier: str, confirm_tf_event: str | None = None) -> str:
    arrow = "🟢 BUY" if ob.direction == "bullish" else "🔴 SELL"
    ote_flag = "✅ inside OTE" if ob.overlaps_ote else "⚠️ outside OTE"
    confirm_line = (
        f"\n*Confirmation:* {confirm_tf_event} on {config.CONFIRM_TF}"
        if confirm_tf_event
        else ""
    )
    return (
        f"{arrow} — *{ob.symbol}* ({tier})\n"
        f"Order block zone: `{ob.zone_low:.2f} - {ob.zone_high:.2f}`\n"
        f"Formed: {ob.formed_at} via {ob.event} on {config.STRUCTURE_TF}\n"
        f"OTE band: `{min(ob.ote_low, ob.ote_high):.2f} - {max(ob.ote_low, ob.ote_high):.2f}` ({ote_flag})"
        f"{confirm_line}\n"
        f"_Not financial advice — verify on your chart before entering._"
    )
