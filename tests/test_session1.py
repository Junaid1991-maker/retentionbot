"""Session 1 tests — Node 1 (intake) + Node 2 (scheduler). Run: pytest -v

Uses a temporary local JSON store, so no Google Sheet or internet needed.
"""
from datetime import date, timedelta

import pytest

import sheets
from graph import intake_graph, schedule_graph
from sheets import QUEUE, LocalStore

TODAY = date(2026, 10, 7)


@pytest.fixture(autouse=True)
def fresh_store(tmp_path):
    store = LocalStore(str(tmp_path / "store.json"))
    sheets.set_store(store)
    yield store
    sheets.set_store(None)


def order(**overrides):
    base = dict(buyer_name="Ahmed", company="Dubai Laundry", phone="+92 318 3355708",
                product="Bath Towels 500gsm", quantity=500, order_value_usd=1525, market="gulf")
    base.update(overrides)
    return base


def add(days_ago=0, **kw):
    return intake_graph.invoke({"order": order(delivery_date=TODAY - timedelta(days=days_ago), **kw),
                                "today": TODAY})


def run(on=TODAY):
    return schedule_graph.invoke({"today": on})


# ---------- Node 1 ----------

def test_new_delivery_gets_day7(fresh_store):
    out = add(0)
    b = out["buyer"]
    assert b["buyer_id"] == "RB-0001"
    assert b["followup_day"] == 7
    assert b["next_followup_date"] == (TODAY + timedelta(days=7)).isoformat()
    assert b["status"] == "active"
    assert b["phone"] == "+923183355708"  # spaces stripped
    assert b["market"] == "Gulf"           # normalised
    assert len(fresh_store.get_all(QUEUE)) == 1


def test_backfill_picks_next_touchpoint():
    assert add(30)["buyer"]["followup_day"] == 30
    assert add(45, product="Other")["buyer"]["followup_day"] == 60


def test_older_than_90_days_is_completed():
    b = add(120)["buyer"]
    assert b["status"] == "completed" and b["followup_day"] is None


def test_duplicate_not_added_twice(fresh_store):
    add(0)
    out = add(0)
    assert out["duplicate"] is True
    assert len(fresh_store.get_all(QUEUE)) == 1


@pytest.mark.parametrize("bad", [{"phone": "123"}, {"quantity": 0}, {"order_value_usd": -5}])
def test_invalid_orders_rejected(bad):
    out = intake_graph.invoke({"order": order(**bad), "today": TODAY})
    assert out["error"]


def test_future_delivery_rejected():
    assert add(-3)["error"]


# ---------- Node 2 ----------

def test_demo_buyers_due_today():
    add(7, buyer_name="Ahmed")
    add(30, buyer_name="Mohammed", product="Hand Towels")
    add(60, buyer_name="Sarah", product="Bed Linen", market="EU")
    out = run()
    assert {b["buyer_name"]: b["message_type"] for b in out["due_buyers"]} == {
        "Ahmed": "day7", "Mohammed": "day30", "Sarah": "day60"}
    # Sarah is 60 days silent, but her touchpoint wins today -> no double message
    assert out["silence_alerts"] == []


def test_nothing_due_between_touchpoints():
    add(0)
    assert run(TODAY + timedelta(days=3))["due_buyers"] == []


def test_missed_day_is_caught_up():
    add(0)
    out = run(TODAY + timedelta(days=9))  # Day 7 was 2 days ago
    assert [b["message_type"] for b in out["due_buyers"]] == ["day7"]


def test_silence_after_45_days_no_reply(fresh_store):
    add(0)
    # pretend Day 7 + Day 30 were sent; next is Day 60
    fresh_store.update(QUEUE, "Buyer ID", "RB-0001", {
        "Next Followup": (TODAY + timedelta(days=60)).isoformat(), "Next Followup Day": 60})
    out = run(TODAY + timedelta(days=45))
    assert [b["buyer_id"] for b in out["silence_alerts"]] == ["RB-0001"]
    assert out["due_buyers"] == []


def test_recent_reply_resets_silence(fresh_store):
    add(0)
    fresh_store.update(QUEUE, "Buyer ID", "RB-0001", {
        "Next Followup": (TODAY + timedelta(days=60)).isoformat(), "Next Followup Day": 60,
        "Last Reply": (TODAY + timedelta(days=40)).isoformat()})
    assert run(TODAY + timedelta(days=50))["silence_alerts"] == []


def test_silence_fires_only_once(fresh_store):
    add(0)
    fresh_store.update(QUEUE, "Buyer ID", "RB-0001", {
        "Next Followup": (TODAY + timedelta(days=60)).isoformat(), "Next Followup Day": 60,
        "Silence Alerted": "yes"})
    assert run(TODAY + timedelta(days=50))["silence_alerts"] == []


@pytest.mark.parametrize("status", ["opted_out", "reorder", "completed", "silent"])
def test_inactive_buyers_skipped(fresh_store, status):
    add(7)
    fresh_store.update(QUEUE, "Buyer ID", "RB-0001", {"Status": status})
    out = run()
    assert out["due_buyers"] == [] and out["silence_alerts"] == []
