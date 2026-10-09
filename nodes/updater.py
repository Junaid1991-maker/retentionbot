"""NODE 5 — Sheets Updater.

After each send, record what happened in Google Sheets.

Sent OK, Day 7/30/60 : Followups Sent +1, Next Followup = delivery date + next day
                        (anchored to delivery, so a late catch-up never shifts the plan)
Sent OK, Day 90      : Followups Sent 4, Status = completed, no next date
Sent OK, silence     : Silence Alerted = yes, row added to Silent Buyers ("at risk").
                        Next Followup is NOT changed (decision A: sequence continues)
Send FAILED          : schedule NOT changed → buyer is still due, retried next run
Always               : one row in Message Log (sent or FAILED + reason)
"""
from datetime import date, timedelta

from sheets import MESSAGE_LOG, QUEUE, SILENT, get_store


def _d(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def sheets_updater(state: dict) -> dict:
    today: date = state.get("today") or date.today()
    store = get_store()
    queue = {r["Buyer ID"]: r for r in store.get_all(QUEUE)}
    updated = 0

    for m in state.get("sent", []):
        bid = m.get("buyer_id")
        row = queue.get(bid, {})
        ok = m.get("message_sent", False)

        store.append(MESSAGE_LOG, {
            "Date": m.get("timestamp", today.isoformat()),
            "Buyer ID": bid,
            "Name": m["buyer_name"],
            "Day Number": m["followup_day"] if m["followup_day"] else "silence",
            "Message Type": m["message_type"],
            "Message Sent": m["personalized_message"],
            "Reply Received": "no",
            "Reply Text": "",
            "Reply Classification": "",
            "Action Taken": (f"sent ({m.get('source', '')})" if ok else f"FAILED: {m.get('error', '')}"),
            "WA Message ID": m.get("wa_message_id", ""),
        })
        if not ok or not row:
            continue

        last_heard = _d(row.get("Last Reply")) or _d(row.get("Delivery Date"))
        fields = {
            "Last Contacted": today.isoformat(),
            "Days Since Last Reply": (today - last_heard).days if last_heard else "",
        }

        if m["is_silence_alert"]:
            fields["Silence Alerted"] = "yes"
            store.append(SILENT, {
                "Buyer ID": bid,
                "Name": m["buyer_name"],
                "Company": row.get("Company", ""),
                "Phone": row.get("Phone", ""),
                "Last Contact": last_heard.isoformat() if last_heard else "",
                "Days Silent": fields["Days Since Last Reply"],
                "Re-engagement Sent": today.isoformat(),
                "Response": "",
                "Status": "at risk",
            })
        else:
            fields["Followups Sent"] = int(row.get("Followups Sent") or 0) + 1
            nxt = m["next_followup_day"]
            delivery = _d(row.get("Delivery Date"))
            if nxt and delivery:
                fields["Next Followup"] = (delivery + timedelta(days=nxt)).isoformat()
                fields["Next Followup Day"] = nxt
            else:  # Day 90 done
                fields["Next Followup"] = ""
                fields["Next Followup Day"] = ""
                fields["Status"] = "completed"

        store.update(QUEUE, "Buyer ID", bid, fields)
        updated += 1

    return {"sheets_updated": updated}
