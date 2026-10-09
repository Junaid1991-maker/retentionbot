"""Read the AI-written messages before anything is sent.

  python preview_messages.py            -> today's due buyers from the Sheet
  python preview_messages.py --samples  -> 5 fixed test cases (all touchpoints,
                                           Gulf + EU, towel + garment, small + big)
Nothing is sent. Nothing in the Sheet changes.
"""
import sys
from datetime import date

import config
from graph import preview_graph
from nodes.generator import generate_message

SAMPLES = [
    ("day7", dict(buyer_name="Ahmed Al-Rashid", company="Dubai Laundry Services LLC",
                  product="Bath Towels 500gsm White", quantity=500, order_value_usd=1525, market="Gulf")),
    ("day30", dict(buyer_name="Mohammed Hassan", company="Riyadh Hotel Supplies",
                   product="Hand Towels 400gsm", quantity=1000, order_value_usd=1200, market="Gulf")),
    ("day60", dict(buyer_name="Lena Fischer", company="Hamburg Basics GmbH",
                   product="Organic Cotton T-Shirts", quantity=2000, order_value_usd=7800, market="EU")),
    ("day90", dict(buyer_name="Sarah Klein", company="Berlin Linen GmbH",
                   product="Bed Linen 200TC", quantity=200, order_value_usd=3000, market="EU")),
    ("silence", dict(buyer_name="Khalid Mansoor", company="Sharjah Spa Supplies",
                     product="Bathrobes Terry 380gsm", quantity=150, order_value_usd=850, market="Gulf",
                     followup_day=60)),
]


def show(m: dict) -> None:
    flag = "AI" if m["source"] == "groq" else f"TEMPLATE ({m['note']})"
    print("=" * 70)
    print(f"{m['buyer_name']}  ·  {m['message_type']}  ·  next: {m['next_followup_day']}  ·  {flag}")
    print("-" * 70)
    print(m["personalized_message"])
    print(f"[{len(m['personalized_message'])} chars]")


if __name__ == "__main__":
    print(f"Groq: {'ON (' + config.GROQ_MODEL + ')' if config.GROQ_API_KEY else 'OFF — showing templates only'}\n")
    if "--samples" in sys.argv:
        for mtype, buyer in SAMPLES:
            show(generate_message(buyer, mtype))
    else:
        out = preview_graph.invoke({"today": date.today()})
        if not out["messages"]:
            print("Nobody is due today.")
        for m in out["messages"]:
            show(m)
    print("=" * 70)
