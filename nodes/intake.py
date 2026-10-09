"""NODE 1 — Order Intake.

Validates a delivered order, works out which touchpoint (Day 7/30/60/90) is
next, and writes the buyer into the Retention Queue tab.

Normal use: delivery_date = today -> first follow-up is Day 7.
Backfill/demo: a past delivery_date picks the next touchpoint still ahead
(e.g. delivered 30 days ago -> Day 30 due today).
"""
from datetime import date, timedelta
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

import config
from sheets import QUEUE, get_store

MARKETS = {"gulf": "Gulf", "eu": "EU", "europe": "EU", "us": "US", "usa": "US"}


class DeliveredOrder(BaseModel):
    buyer_name: str = Field(min_length=1)
    company: str = ""
    phone: str
    product: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    order_value_usd: float = Field(gt=0)
    market: str = "Other"
    delivery_date: Optional[date] = None  # None -> today

    @field_validator("phone")
    @classmethod
    def normalise_phone(cls, v: str) -> str:
        digits = "".join(c for c in v if c.isdigit())
        if len(digits) < 10:
            raise ValueError("phone number looks too short")
        return "+" + digits

    @field_validator("market")
    @classmethod
    def normalise_market(cls, v: str) -> str:
        v = (v or "").strip()
        return MARKETS.get(v.lower(), v or "Other")


def next_touchpoint(delivery: date, today: date) -> tuple[Optional[int], Optional[date]]:
    """First Day-N touchpoint that is today or later. (None, None) if all passed."""
    for day in config.FOLLOWUP_DAYS:
        due = delivery + timedelta(days=day)
        if due >= today:
            return day, due
    return None, None


def order_intake(state: dict) -> dict:
    today: date = state.get("today") or date.today()

    try:
        order = DeliveredOrder(**state["order"])
    except ValidationError as e:
        return {"error": f"Invalid order: {e.errors()[0]['loc'][0]} — {e.errors()[0]['msg']}"}

    delivery = order.delivery_date or today
    if delivery > today:
        return {"error": "delivery_date is in the future — only add delivered orders"}

    store = get_store()
    rows = store.get_all(QUEUE)

    # Duplicate guard: same phone + product + delivery date already queued
    for r in rows:
        if (r["Phone"] == order.phone and r["Product"] == order.product
                and r["Delivery Date"] == delivery.isoformat()):
            return {"buyer": _row_to_buyer(r), "duplicate": True}

    day, due = next_touchpoint(delivery, today)
    row = {
        "Buyer ID": f"RB-{len(rows) + 1:04d}",
        "Name": order.buyer_name,
        "Company": order.company,
        "Phone": order.phone,
        "Product": order.product,
        "Quantity": order.quantity,
        "Order Value USD": order.order_value_usd,
        "Delivery Date": delivery.isoformat(),
        "Market": order.market,
        "Last Contacted": "",
        "Last Reply": "",
        "Followups Sent": 0,
        "Next Followup": due.isoformat() if due else "",
        "Next Followup Day": day or "",
        "Days Since Last Reply": (today - delivery).days,
        "Status": "active" if day else "completed",
        "Silence Alerted": "",
    }
    store.append(QUEUE, row)
    return {"buyer": _row_to_buyer(row), "duplicate": False}


def _row_to_buyer(r: dict) -> dict:
    """Sheet row -> Node 1 output shape from the context doc."""
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
        "next_followup_date": r["Next Followup"] or None,
        "followup_day": int(r["Next Followup Day"]) if str(r["Next Followup Day"]) else None,
        "status": r["Status"],
    }
