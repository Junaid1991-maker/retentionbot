"""Reply handling (Workflow 2) — what happens when a buyer answers on WhatsApp.

Graph:  match_buyer -> reply_classifier (Node 6) -> reply_actions

match_buyer
  1. reply-to: the reply quotes one of our messages → look up that WA Message ID
     in Message Log → exact buyer. Works in demo and live.
  2. phone: live mode only — buyer's number in Retention Queue (status active or
     completed; newest delivery wins if they have several orders).
  DEMO_MODE: all demo buyers share one phone, so ONLY reply-to counts (same as
  NurtureBot). A plain "Hi" from your phone is passed on to the Order Agent.

reply_actions (always): Last Reply = today, Message Log row gets the reply,
  Silent Buyers "at risk" → "recovered".
  REORDER → status reorder, Reorders row, alert Junaid, ack to buyer, handoff=True
  INQUIRY → holding reply to buyer + alert Junaid (a human answers prices)
  STOP    → status opted_out + one confirmation message
  NEUTRAL → short warm reply
"""
from collections import OrderedDict
from datetime import date

import config
from nodes.classifier import classify_reply
from nodes.generator import first_name, is_gulf, unit
from nodes.sender import send_alert, send_text
from sheets import MESSAGE_LOG, QUEUE, REORDERS, SILENT, get_store

MATCH_STATUSES = ("active", "completed")   # completed = Day 90 sent; a reply to it is gold

_seen: "OrderedDict[str, bool]" = OrderedDict()   # Meta can deliver the same webhook twice


def _digits(p) -> str:
    return "".join(c for c in str(p) if c.isdigit())


def already_seen(msg_id: str) -> bool:
    if not msg_id:
        return False
    if msg_id in _seen:
        return True
    _seen[msg_id] = True
    if len(_seen) > 500:
        _seen.popitem(last=False)
    return False


# ---------- parsing Meta webhook JSON ----------

def parse_meta(payload: dict) -> dict | None:
    """Pull the first text message out of a Meta webhook body. None if not a text message."""
    try:
        value = payload["entry"][0]["changes"][0]["value"]
        msg = value["messages"][0]
    except (KeyError, IndexError, TypeError):
        return None  # status callbacks (sent/delivered/read) land here
    if msg.get("type") != "text":
        return None
    return {
        "phone": msg.get("from", ""),
        "text": msg.get("text", {}).get("body", ""),
        "msg_id": msg.get("id", ""),
        "context_id": (msg.get("context") or {}).get("id", ""),
    }


# ---------- node: match_buyer ----------

def find_buyer(phone: str, context_id: str = "") -> dict | None:
    store = get_store()
    queue = store.get_all(QUEUE)
    log_row = None

    if context_id:
        log_row = next((r for r in store.get_all(MESSAGE_LOG)
                        if r.get("WA Message ID") == context_id), None)
        if log_row:
            row = next((r for r in queue if r["Buyer ID"] == log_row["Buyer ID"]), None)
            if row and row["Status"] in MATCH_STATUSES:
                return {"row": row, "log_row": log_row}
            return None

    if config.DEMO_MODE:
        return None  # demo: shared phone → reply-to only

    mine = [r for r in queue if _digits(r["Phone"]) == _digits(phone) and r["Status"] in MATCH_STATUSES]
    if not mine:
        return None
    row = max(mine, key=lambda r: r["Delivery Date"])
    logs = [r for r in store.get_all(MESSAGE_LOG) if r["Buyer ID"] == row["Buyer ID"] and r.get("WA Message ID")]
    return {"row": row, "log_row": logs[-1] if logs else None}


def match_buyer(state: dict) -> dict:
    found = find_buyer(state["phone"], state.get("context_id", ""))
    if not found:
        return {"handled": False, "reason": "not a retention buyer reply"}
    return {"handled": True, "row": found["row"], "log_row": found["log_row"]}


# ---------- node: reply_classifier ----------

def reply_classifier(state: dict) -> dict:
    context = (state.get("log_row") or {}).get("Message Sent", "")
    return {"result": classify_reply(state["text"], context)}


# ---------- node: reply_actions ----------

def _replies(row: dict) -> dict:
    name = first_name(row["Name"])
    qty = f"{int(row['Quantity']):,} {unit(row['Product'])}"
    if is_gulf(row["Market"]):
        return {
            "REORDER": f"Shukriya {name} bhai! 🙏 Noted — same {qty} of {row['Product']}. "
                       f"I'll confirm the details and send your proforma shortly.",
            "INQUIRY": f"Thanks {name} bhai, good question! Let me check the latest details and get back to you shortly.",
            "STOP": f"Understood {name} bhai — we won't send further updates. Thank you for your business. 🙏",
            "NEUTRAL": f"Shukriya {name} bhai! Always here if you need anything. 🙏",
        }
    return {
        "REORDER": f"Thank you, {name}! Noted — {qty} of {row['Product']}, same as last time. "
                   f"I'll confirm the details and send a proforma shortly.",
        "INQUIRY": f"Thank you, {name} — good question. Let me check the latest details and come back to you shortly.",
        "STOP": f"Understood, {name} — we won't send further updates. Thank you for your business.",
        "NEUTRAL": f"Thank you, {name}. Always here if you need anything.",
    }


def reply_actions(state: dict) -> dict:
    today: date = state.get("today") or date.today()
    store = get_store()
    row, log_row, res = state["row"], state.get("log_row"), state["result"]
    label, bid, text = res["classification"], row["Buyer ID"], state["text"]
    reply_to = config.DEMO_WA_NUMBER if config.DEMO_MODE else row["Phone"]
    original = (f"{int(row['Quantity']):,} {unit(row['Product'])} {row['Product']} · "
                f"USD {float(row['Order Value USD']):,.0f}")
    via = f"Day {log_row['Day Number']}" if log_row and str(log_row.get("Day Number")) != "silence" else "silence msg"

    # --- always ---
    fields = {"Last Reply": today.isoformat(), "Days Since Last Reply": 0}
    if label == "REORDER":
        fields["Status"] = "reorder"
    elif label == "STOP":
        fields["Status"] = "opted_out"
    store.update(QUEUE, "Buyer ID", bid, fields)

    for s in store.get_all(SILENT):
        if s["Buyer ID"] == bid and s["Status"] == "at risk":
            store.update(SILENT, "Buyer ID", bid, {"Status": "recovered", "Response": text[:200]})

    # --- per label ---
    alert = None
    if label == "REORDER":
        store.append(REORDERS, {
            "Date": today.isoformat(), "Buyer ID": bid, "Name": row["Name"], "Company": row["Company"],
            "Phone": row["Phone"], "Original Order": original, "Reply Text": text,
            "Handed to Order Agent": "Y", "New Order Value": "", "Notes": f"reply to {via}",
        })
        alert = (f"🔥 REORDER: {row['Name']} ({row['Company']}) replied to {via}:\n\"{text}\"\n"
                 f"Last order: {original}. Handed to Order Agent.")
    elif label == "INQUIRY":
        alert = (f"💬 Question from {row['Name']} ({row['Company']}) on {via}:\n\"{text}\"\n"
                 f"Holding reply sent — please answer personally.")

    ack = send_text(reply_to, _replies(row)[label]) if config.AUTO_REPLY else {"message_sent": False}
    alerted = send_alert(alert)["message_sent"] if alert else False

    action = {"REORDER": "REORDER → alerted, logged, handed to Order Agent",
              "INQUIRY": "INQUIRY → holding reply + alert",
              "STOP": "STOP → opted out",
              "NEUTRAL": "NEUTRAL → warm reply"}[label]
    if log_row and log_row.get("WA Message ID"):
        store.update(MESSAGE_LOG, "WA Message ID", log_row["WA Message ID"], {
            "Reply Received": "yes", "Reply Text": text[:500],
            "Reply Classification": f"{label} ({res['source']}, {res['confidence']:.2f})",
            "Action Taken": f"{log_row.get('Action Taken', '')} | {action}",
        })

    return {
        "handoff_to_order_agent": label == "REORDER",
        "order_context": {
            "buyer_id": bid, "buyer_name": row["Name"], "company": row["Company"], "phone": row["Phone"],
            "product": row["Product"], "quantity": int(row["Quantity"]),
            "last_order_value_usd": float(row["Order Value USD"]), "market": row["Market"],
            "reply_text": text,
            # what n8n feeds the Order Agent instead of "need same order again",
            # so its extractor finds item + quantity + GSM and makes the proforma
            "order_message": f"{int(row['Quantity'])} {unit(row['Product'])} {row['Product']}, same as last order",
        } if label == "REORDER" else None,
        "auto_reply_sent": ack.get("message_sent", False),
        "alert_sent": alerted,
    }
