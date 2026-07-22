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


def calc_position_size(risk_per_share: float) -> int:
    """
    Shared position-sizing formula used by alerts, plots, and the executor.
    Returns whole shares sized so a stop-out risks RISK_PER_TRADE_PCT of ACCOUNT_EQUITY.
    Returns 0 if risk_per_share is zero or negative.
    """
    if risk_per_share <= 0:
        return 0
    budget = config.ACCOUNT_EQUITY * (config.RISK_PER_TRADE_PCT / 100)
    return int(budget / risk_per_share)


def calc_ob_levels(ob) -> dict:
    """Extract entry/SL/TP levels from an OrderBlock. Shared by alert formatter and TV queue.

    TP1 = 2:1 R/R, TP2 = 3:1 R/R (fixed multiples so targets are always meaningful).
    """
    ote_mid = (min(ob.ote_low, ob.ote_high) + max(ob.ote_low, ob.ote_high)) / 2
    atr_buf = 0.25 * ob.atr_at_formation
    if ob.direction == "bullish":
        entry = ote_mid
        sl = ob.zone_low - atr_buf
        risk = entry - sl
        tp1 = entry + 2 * risk
        tp2 = entry + 3 * risk
    else:
        entry = ote_mid
        sl = ob.zone_high + atr_buf
        risk = sl - entry
        tp1 = entry - 2 * risk
        tp2 = entry - 3 * risk
    return {"entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "risk": risk, "rr": 2.0}


def format_ob_alert(
    ob,
    tier: str,
    confirm_tf_event: str | None = None,
    exec_result: dict | None = None,
) -> str:
    arrow = "🟢 BUY" if ob.direction == "bullish" else "🔴 SELL"
    ote_flag = "✅ inside OTE" if ob.overlaps_ote else "⚠️ outside OTE"
    confirm_line = (
        f"\n*Confirmation:* {confirm_tf_event} on {config.CONFIRM_TF}"
        if confirm_tf_event
        else ""
    )

    lvl = calc_ob_levels(ob)
    entry, sl, tp1, tp2, risk = lvl["entry"], lvl["sl"], lvl["tp1"], lvl["tp2"], lvl["risk"]

    shares = calc_position_size(risk)
    dollar_risk = shares * risk

    # Execution status line
    if exec_result is None:
        exec_line = "\n🔔 _Alert only — auto-execution disabled_"
    elif exec_result["status"] == "submitted":
        exec_line = f"\n✅ *Auto-submitted* — order `{exec_result['order_id']}`"
    elif exec_result["status"] == "skipped":
        reason_map = {
            "kill_switch_active": "kill switch active",
            "daily_cap_reached": "daily trade cap reached",
            "position_cap_reached": "position cap reached",
            "zero_shares": "position size = 0 (risk too small)",
            "auto_execute_disabled": "auto-execution disabled",
            "live_trading_not_confirmed": "live trading not confirmed",
        }
        reason = exec_result.get("reason", "unknown")
        label = reason_map.get(reason.split(" ")[0], reason)
        exec_line = f"\n⏭ *Auto-execution skipped* — {label}"
    else:
        exec_line = f"\n⚠️ *Order error* — {exec_result.get('reason', 'unknown')}"

    return (
        f"{arrow} — *{ob.symbol}* ({tier})\n"
        f"Order block zone: `{ob.zone_low:.2f} - {ob.zone_high:.2f}`\n"
        f"Formed: {ob.formed_at} via {ob.event} on {config.STRUCTURE_TF}\n"
        f"OTE band: `{min(ob.ote_low, ob.ote_high):.2f} - {max(ob.ote_low, ob.ote_high):.2f}` ({ote_flag})"
        f"{confirm_line}\n"
        f"\n"
        f"📍 *Entry:* `{entry:.2f}` (OTE mid, limit)\n"
        f"🛑 *Stop Loss:* `{sl:.2f}` (zone far side − 0.25×ATR)\n"
        f"🎯 *TP1:* `{tp1:.2f}` (2R — informational)\n"
        f"🎯 *TP2:* `{tp2:.2f}` (3R — bracket target)\n"
        f"📐 *Suggested size:* `{shares:,} sh` (risking {config.RISK_PER_TRADE_PCT:.1f}% / ${dollar_risk:,.0f} of ${config.ACCOUNT_EQUITY:,.0f})\n"
        f"{exec_line}\n"
        f"_Not financial advice — verify on your chart before entering._"
    )
