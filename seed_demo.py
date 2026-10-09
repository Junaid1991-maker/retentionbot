"""Load the 3 demo buyers from the context doc, then show who is due today.

  python seed_demo.py
Expected: Ahmed -> day7, Mohammed -> day30, Sarah -> day60. Re-running is
safe (duplicates are detected, not added twice).
"""
from datetime import date, timedelta

import config
from graph import intake_graph, schedule_graph

TODAY = date.today()
DEMO = config.DEMO_WA_NUMBER

BUYERS = [
    dict(buyer_name="Ahmed Al-Rashid", company="Dubai Laundry Services LLC", phone=DEMO,
         product="Bath Towels 500gsm White", quantity=500, order_value_usd=1525,
         market="Gulf", delivery_date=TODAY - timedelta(days=7)),
    dict(buyer_name="Mohammed Hassan", company="Riyadh Hotel Supplies", phone=DEMO,
         product="Hand Towels 400gsm", quantity=1000, order_value_usd=1200,
         market="Gulf", delivery_date=TODAY - timedelta(days=30)),
    dict(buyer_name="Sarah Klein", company="Berlin Linen GmbH", phone=DEMO,
         product="Bed Linen 200TC", quantity=200, order_value_usd=3000,
         market="EU", delivery_date=TODAY - timedelta(days=60)),
]

if __name__ == "__main__":
    for order in BUYERS:
        out = intake_graph.invoke({"order": order, "today": TODAY})
        if out.get("error"):
            print("❌", order["buyer_name"], out["error"])
            continue
        b = out["buyer"]
        tag = "already in queue" if out["duplicate"] else "added"
        print(f"{b['buyer_id']}  {b['buyer_name']:<16} {tag:<16} next: Day {b['followup_day']} on {b['next_followup_date']}")

    run = schedule_graph.invoke({"today": TODAY})
    print(f"\nDue today ({TODAY}): {len(run['due_buyers'])}")
    for b in run["due_buyers"]:
        print(f"  {b['buyer_name']:<16} {b['message_type']}")
    print(f"Silence alerts: {len(run['silence_alerts'])}")
    for b in run["silence_alerts"]:
        print(f"  {b['buyer_name']:<16} {b['days_since_last_reply']} days silent")
