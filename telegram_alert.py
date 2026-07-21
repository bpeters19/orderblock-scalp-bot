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


def calc_ob_levels(ob) -> dict:
    """Extract entry/SL/TP levels from an OrderBlock. Shared by alert formatter and TV queue."""
    ote_mid = (min(ob.ote_low, ob.ote_high) + max(ob.ote_low, ob.ote_high)) / 2
    atr_buf = 0.25 * ob.atr_at_formation
    if ob.direction == "bullish":
        entry = ote_mid
        sl = ob.zone_low - atr_buf
        risk = entry - sl
        tp1 = ob.leg_end
        tp2 = tp1 + risk
    else:
        entry = ote_mid
        sl = ob.zone_high + atr_buf
        risk = sl - entry
        tp1 = ob.leg_end
        tp2 = tp1 - risk
    rr = round((tp1 - entry) / risk, 1) if ob.direction == "bullish" else round((entry - tp1) / risk, 1)
    return {"entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "risk": risk, "rr": rr}


def format_ob_alert(ob, tier: str, confirm_tf_event: str | None = None) -> str:
    arrow = "🟢 BUY" if ob.direction == "bullish" else "🔴 SELL"
    ote_flag = "✅ inside OTE" if ob.overlaps_ote else "⚠️ outside OTE"
    confirm_line = (
        f"\n*Confirmation:* {confirm_tf_event} on {config.CONFIRM_TF}"
        if confirm_tf_event
        else ""
    )

    lvl = calc_ob_levels(ob)
    entry, sl, tp1, tp2, actual_rr = lvl["entry"], lvl["sl"], lvl["tp1"], lvl["tp2"], lvl["rr"]

    risk_dollars_budget = config.ACCOUNT_EQUITY * (config.RISK_PER_TRADE_PCT / 100)
    shares = int(risk_dollars_budget / risk) if risk > 0 else 0
    dollar_risk = shares * risk

    return (
        f"{arrow} — *{ob.symbol}* ({tier})\n"
        f"Order block zone: `{ob.zone_low:.2f} - {ob.zone_high:.2f}`\n"
        f"Formed: {ob.formed_at} via {ob.event} on {config.STRUCTURE_TF}\n"
        f"OTE band: `{min(ob.ote_low, ob.ote_high):.2f} - {max(ob.ote_low, ob.ote_high):.2f}` ({ote_flag})"
        f"{confirm_line}\n"
        f"\n"
        f"📍 *Entry:* `{entry:.2f}` (OTE mid)\n"
        f"🛑 *Stop Loss:* `{sl:.2f}` (zone far side − 0.25×ATR)\n"
        f"🎯 *TP1:* `{tp1:.2f}` (broken swing · {actual_rr}R)\n"
        f"🎯 *TP2:* `{tp2:.2f}` (extended +1R)\n"
        f"📐 *Suggested size:* `{shares:,} sh` (risking {config.RISK_PER_TRADE_PCT:.1f}% / ${dollar_risk:,.0f} of ${config.ACCOUNT_EQUITY:,.0f})\n"
        f"_Not financial advice — verify on your chart before entering._"
    )
