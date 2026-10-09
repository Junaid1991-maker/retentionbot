"""RetentionBot dashboard — 4 tabs, read-only view of the Google Sheet.

Local:   streamlit run streamlit_app/app.py
Render:  streamlit run streamlit_app/app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true
Static tables (st.table) on purpose: lighter on Render free tier, same as NurtureBot.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

import config  # noqa: E402
from sheets import MESSAGE_LOG, QUEUE, REORDERS, SILENT, get_store  # noqa: E402
from streamlit_app.data import active_rows, log_rows, metrics  # noqa: E402

st.set_page_config(page_title="RetentionBot", page_icon="🔁", layout="wide")

# ---------- login ----------
if config.DASHBOARD_PASSWORD and not st.session_state.get("ok"):
    st.title("🔁 RetentionBot")
    pw = st.text_input("Password", type="password")
    if pw:
        if pw == config.DASHBOARD_PASSWORD:
            st.session_state["ok"] = True
            st.rerun()
        else:
            st.error("Wrong password")
    st.stop()


@st.cache_data(ttl=60, show_spinner="Reading Google Sheet…")
def load():
    s = get_store()
    return s.get_all(QUEUE), s.get_all(MESSAGE_LOG), s.get_all(REORDERS), s.get_all(SILENT)


def table(rows, empty="Nothing here yet."):
    if rows:
        st.table(pd.DataFrame(rows))
    else:
        st.info(empty)


# ---------- header ----------
left, right = st.columns([5, 1])
left.title("🔁 RetentionBot")
left.caption(f"Stage 7 · Customer retention · Day 7 / 30 / 60 / 90 follow-ups"
             f"{' · DEMO MODE (all messages go to the demo phone)' if config.DEMO_MODE else ''}")
if right.button("↻ Refresh"):
    load.clear()

try:
    queue, log, reorders, silent = load()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not read the Google Sheet: {e}")
    st.stop()

m = metrics(queue, log, reorders, silent)
c = st.columns(6)
c[0].metric("Active buyers", m["active"])
c[1].metric("Messages today", m["messages_today"])
c[2].metric("Reorders", m["reorders"])
c[3].metric("⚠️ At risk", m["at_risk"])
c[4].metric("Reply rate", f"{m['reply_rate']}%")
c[5].metric("Repeat business (est.)", f"USD {m['repeat_value_usd']:,.0f}")

t1, t2, t3, t4 = st.tabs(["Active Buyers", "🔥 Reorders", "⚠️ Silent Buyers", "Message Log"])

with t1:
    table(active_rows(queue), "No buyers in the queue. Add one with POST /add-buyer.")

with t2:
    table([{
        "Date": r.get("Date"), "Buyer": r.get("Name"), "Company": r.get("Company"),
        "Original order": r.get("Original Order"), "Reply": r.get("Reply Text"),
        "To Order Agent": r.get("Handed to Order Agent"), "Notes": r.get("Notes"),
    } for r in reversed(reorders)], "No reorders yet — they appear here the moment a buyer replies.")

with t3:
    at_risk = [s for s in silent if s.get("Status") == "at risk"]
    if at_risk:
        st.warning(f"{len(at_risk)} buyer(s) silent 45+ days — worth a personal call.")
    table([{
        "Buyer": s.get("Name"), "Company": s.get("Company"), "Phone": s.get("Phone"),
        "Days silent": s.get("Days Silent"), "Re-engagement sent": s.get("Re-engagement Sent"),
        "Response": s.get("Response") or "—", "Status": s.get("Status"),
    } for s in reversed(silent)], "No silent buyers. 🎉")

with t4:
    table(log_rows(log), "No messages sent yet.")

st.caption("Data refreshes every 60 s · Source: Google Sheet 'RetentionBot' · Built by Automiq")
