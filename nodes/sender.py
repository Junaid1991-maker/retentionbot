"""NODE 4 — WhatsApp Sender.

POSTs each generated message to the Meta WhatsApp Cloud API (same token and
phone number ID as all other agents).

DEMO_MODE=true  -> every message goes to DEMO_WA_NUMBER (your phone), with a
                   one-line header saying which buyer/touchpoint it was for.
DEMO_MODE=false -> messages go to the buyer's real phone.

Never raises: a failed send comes back as message_sent=False + error, so the
updater can log it and leave the buyer due for tomorrow.
"""
import time
from datetime import datetime

import httpx

import config

ERROR_HINTS = {
    131047: "outside the 24h window — send 'Hi' from the receiving phone to the test number first "
            "(live mode needs an approved template)",
    131026: "receiver can't get messages (not on WhatsApp / not a test recipient)",
    131030: "number not in the test number's allowed recipient list (Meta > WhatsApp > API Setup)",
    190: "access token invalid or expired — check WA_ACCESS_TOKEN",
    100: "bad request — check WA_PHONE_NUMBER_ID",
    130429: "rate limited by Meta — slow down",
}


def _digits(phone: str) -> str:
    return "".join(c for c in phone if c.isdigit())


def send_text(to: str, body: str) -> dict:
    """Send one text message. Returns {message_sent, wa_message_id, timestamp, error}."""
    ts = datetime.now().isoformat(timespec="seconds")
    if not config.WA_ACCESS_TOKEN:
        return {"message_sent": False, "wa_message_id": "", "timestamp": ts,
                "error": "WA_ACCESS_TOKEN not set in .env"}

    url = f"https://graph.facebook.com/{config.WA_API_VERSION}/{config.WA_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": _digits(to),
        "type": "text",
        "text": {"body": body, "preview_url": False},
    }
    try:
        r = httpx.post(url, json=payload, timeout=20,
                       headers={"Authorization": f"Bearer {config.WA_ACCESS_TOKEN}"})
        data = r.json() if r.content else {}
    except Exception as e:
        return {"message_sent": False, "wa_message_id": "", "timestamp": ts,
                "error": f"network error: {type(e).__name__}: {e}"[:200]}

    if r.status_code == 200 and data.get("messages"):
        return {"message_sent": True, "wa_message_id": data["messages"][0].get("id", ""),
                "timestamp": ts, "error": ""}

    err = data.get("error", {})
    code = err.get("code")
    hint = ERROR_HINTS.get(code, "")
    msg = f"Meta error {code}: {err.get('message', r.text[:150])}" + (f" → {hint}" if hint else "")
    return {"message_sent": False, "wa_message_id": "", "timestamp": ts, "error": msg[:300]}


def recipient_for(buyer_phone: str) -> str:
    return config.DEMO_WA_NUMBER if config.DEMO_MODE else buyer_phone


def demo_wrap(msg: dict) -> str:
    """In demo mode, prefix a header so 3 messages on one phone are easy to tell apart."""
    body = msg["personalized_message"]
    if config.DEMO_MODE and config.DEMO_HEADER:
        label = "silence re-engagement" if msg["is_silence_alert"] else f"Day {msg['followup_day']}"
        body = f"🧪 DEMO → {msg['buyer_name']} · {label}\n\n{body}"
    return body


def send_alert(text: str) -> dict:
    """WhatsApp alert to Junaid (silence now; reorders in Session 4)."""
    return send_text(config.ALERT_WA_NUMBER, text)


def whatsapp_sender(state: dict) -> dict:
    """LangGraph node: send every generated message, pause between sends."""
    results = []
    messages = state.get("messages", [])[: config.MAX_SENDS_PER_RUN]
    for i, m in enumerate(messages):
        if i:
            time.sleep(config.SEND_DELAY_SECONDS)
        res = send_text(recipient_for(m.get("phone") or ""), demo_wrap(m))
        out = {**m, **res, "sent_to": recipient_for(m.get("phone") or "")}

        if m["is_silence_alert"] and res["message_sent"]:
            alert = send_alert(
                f"⚠️ Buyer at risk: {m['buyer_name']} ({m.get('buyer_id', '')}) — no reply in "
                f"{m.get('days_since_last_reply', '45+')} days. Re-engagement message sent. "
                f"Product: {m.get('product', '')}. Worth a personal call?"
            )
            out["alert_sent"] = alert["message_sent"]
        results.append(out)
    return {"sent": results}
