"""Pure data helpers for the dashboard (no Streamlit here, so they can be tested)."""
from datetime import date


def _num(v, default=0.0) -> float:
    try:
        return float(str(v).replace(",", "") or default)
    except ValueError:
        return default


def metrics(queue: list[dict], log: list[dict], reorders: list[dict], silent: list[dict],
            today: date | None = None) -> dict:
    today_s = (today or date.today()).isoformat()
    sent_today = [r for r in log if str(r.get("Date", ""))[:10] == today_s
                  and str(r.get("Action Taken", "")).startswith("sent")]
    replies = [r for r in log if str(r.get("Reply Received", "")).lower() == "yes"]
    sent_all = [r for r in log if str(r.get("Action Taken", "")).startswith("sent")]
    reorder_ids = {r.get("Buyer ID") for r in reorders}
    repeat_value = sum(_num(q.get("Order Value USD")) for q in queue if q.get("Buyer ID") in reorder_ids)
    return {
        "active": sum(q.get("Status") == "active" for q in queue),
        "messages_today": len(sent_today),
        "reorders": len(reorders),
        "at_risk": sum(s.get("Status") == "at risk" for s in silent),
        "reply_rate": round(100 * len(replies) / len(sent_all)) if sent_all else 0,
        "repeat_value_usd": repeat_value,
        "opted_out": sum(q.get("Status") == "opted_out" for q in queue),
        "total_buyers": len(queue),
    }


def active_rows(queue: list[dict]) -> list[dict]:
    rows = [q for q in queue if q.get("Status") in ("active", "completed")]
    rows.sort(key=lambda q: str(q.get("Next Followup") or "9999"))
    return [{
        "Buyer": q.get("Name"), "Company": q.get("Company"), "Product": q.get("Product"),
        "Market": q.get("Market"), "Order USD": f"{_num(q.get('Order Value USD')):,.0f}",
        "Sent": f"{q.get('Followups Sent') or 0}/4",
        "Next": f"Day {q.get('Next Followup Day')} · {q.get('Next Followup')}" if q.get("Next Followup") else "—",
        "Days silent": q.get("Days Since Last Reply"), "Status": q.get("Status"),
    } for q in rows]


def log_rows(log: list[dict], limit: int = 50) -> list[dict]:
    out = []
    for r in reversed(log[-limit:]):
        msg = str(r.get("Message Sent", "")).replace("\n", " ")
        out.append({
            "When": str(r.get("Date", ""))[:16].replace("T", " "), "Buyer": r.get("Name"),
            "Touchpoint": f"Day {r.get('Day Number')}" if str(r.get("Day Number")) != "silence" else "silence",
            "Message": msg[:90] + ("…" if len(msg) > 90 else ""),
            "Reply": r.get("Reply Text") or "—",
            "Class": str(r.get("Reply Classification") or "—").split(" ")[0],
        })
    return out
