"""NODE 2 — Schedule Checker (with silence detection).

Read-only: one read of the Retention Queue, no writes. (Node 5 writes later.)
This keeps the daily run to a single Sheets API call no matter how many buyers.

Rules:
  - Only status == "active" buyers are considered.
  - DUE: Next Followup <= today. "<=" (not "==") so a missed day (server
    asleep, n8n down) is caught up the next morning instead of lost.
  - SILENCE: days since we last heard from the buyer >= SILENCE_THRESHOLD_DAYS.
    "Last heard" = Last Reply date, or Delivery Date if they never replied.
    Fires once per buyer (Silence Alerted column).
  - A due touchpoint always wins over a silence alert on the same day, so a
    buyer never gets two messages in one run.
  - Breathing room: silence only fires if our last message was MIN_GAP_DAYS+ ago
    AND the next touchpoint is MIN_GAP_DAYS+ away. (Stops a silence message landing
    the day after Day 60, or a few days before Day 90.)
"""
from datetime import date

import config
from sheets import QUEUE, get_store


def _d(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _gap_ok(last_contacted: date | None, next_due: date | None, today: date) -> bool:
    gap = config.MIN_GAP_DAYS
    if last_contacted and (today - last_contacted).days < gap:
        return False
    if next_due and (next_due - today).days < gap:
        return False
    return True


def _profile(r: dict, days_silent: int) -> dict:
    return {
        "buyer_id": r["Buyer ID"],
        "buyer_name": r["Name"],
        "company": r["Company"],
        "phone": r["Phone"],
        "product": r["Product"],
        "quantity": int(r["Quantity"]),
        "order_value_usd": float(r["Order Value USD"]),
        "delivery_date": r["Delivery Date"],
        "market": r["Market"],
        "followups_sent": int(r["Followups Sent"] or 0),
        "followup_day": int(r["Next Followup Day"]) if str(r["Next Followup Day"]) else None,
        "next_followup_date": r["Next Followup"] or None,
        "days_since_last_reply": days_silent,
    }


def schedule_checker(state: dict) -> dict:
    today: date = state.get("today") or date.today()
    due, silence = [], []

    for r in get_store().get_all(QUEUE):
        if r["Status"] != "active":
            continue

        last_heard = _d(r["Last Reply"]) or _d(r["Delivery Date"])
        days_silent = (today - last_heard).days if last_heard else 0
        buyer = _profile(r, days_silent)

        next_due = _d(r["Next Followup"])
        if next_due and next_due <= today and buyer["followup_day"]:
            due.append({**buyer, "message_type": f"day{buyer['followup_day']}"})
        elif (days_silent >= config.SILENCE_THRESHOLD_DAYS and r["Silence Alerted"] != "yes"
              and _gap_ok(_d(r["Last Contacted"]), next_due, today)):
            silence.append({**buyer, "message_type": "silence"})

    return {"due_buyers": due, "silence_alerts": silence}
