"""Wipe the 4 tabs (headers stay) and load the 3 demo buyers again,
all due today. Use before every demo / re-test.

  python reset_demo.py
"""
from datetime import date

from graph import intake_graph, schedule_graph
from seed_demo import BUYERS
from sheets import TABS, get_store

if __name__ == "__main__":
    store = get_store()
    for tab in TABS:
        store.clear(tab)
    print("Cleared: " + ", ".join(TABS))
    for order in BUYERS:
        b = intake_graph.invoke({"order": order, "today": date.today()})["buyer"]
        print(f"  {b['buyer_id']}  {b['buyer_name']:<16} Day {b['followup_day']} due {b['next_followup_date']}")
    due = schedule_graph.invoke({"today": date.today()})["due_buyers"]
    print(f"\nReady: {len(due)} buyer(s) due today.")
