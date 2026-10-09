"""Session 3 tests — Node 4 (sender) + Node 5 (updater) + full daily run.

Meta and Groq are faked: nothing is really sent, no internet needed.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import config
import sheets
from graph import daily_graph, intake_graph
from nodes import generator as gen
from nodes import sender
from sheets import MESSAGE_LOG, QUEUE, SILENT, LocalStore

TODAY = date(2026, 10, 7)


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.content = b"x"
        self.text = str(data)

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    store = LocalStore(str(tmp_path / "store.json"))
    sheets.set_store(store)
    monkeypatch.setattr(config, "GROQ_API_KEY", "")            # template messages, no AI
    monkeypatch.setattr(config, "WA_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "SEND_DELAY_SECONDS", 0)
    yield store
    sheets.set_store(None)


@pytest.fixture
def meta(monkeypatch):
    """Fake Meta API. Set meta.fail_for = {'Sarah'} to make sends containing that name fail."""
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers})
        body = json["text"]["body"]
        if any(n in body for n in fake_post.fail_for):
            return FakeResp(400, {"error": {"code": 131047, "message": "Re-engagement message"}})
        return FakeResp(200, {"messages": [{"id": f"wamid.{len(calls)}"}]})
    fake_post.fail_for = set()
    fake_post.calls = calls
    monkeypatch.setattr(sender.httpx, "post", fake_post)
    return fake_post


def add(name, days_ago, product="Bath Towels 500gsm", market="Gulf", phone="+971500000001"):
    return intake_graph.invoke({"today": TODAY, "order": dict(
        buyer_name=name, phone=phone, product=product, quantity=500, order_value_usd=1500,
        market=market, delivery_date=TODAY - timedelta(days=days_ago))})["buyer"]


def row(store, bid):
    return next(r for r in store.get_all(QUEUE) if r["Buyer ID"] == bid)


# ---------- Node 4: sender ----------

def test_send_payload_and_demo_routing(meta):
    add("Ahmed Al-Rashid", 7, phone="+971500000001")
    daily_graph.invoke({"today": TODAY})
    call = meta.calls[0]
    assert call["url"].endswith(f"/{config.WA_PHONE_NUMBER_ID}/messages")
    assert call["headers"]["Authorization"] == "Bearer test-token"
    assert call["json"]["to"] == "923183355708"            # demo number, not the buyer's
    assert call["json"]["text"]["body"].startswith("🧪 DEMO → Ahmed Al-Rashid · Day 7")


def test_live_mode_sends_to_buyer_without_header(meta, monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    add("Ahmed Al-Rashid", 7, phone="+971500000001")
    daily_graph.invoke({"today": TODAY})
    assert meta.calls[0]["json"]["to"] == "971500000001"
    assert "DEMO" not in meta.calls[0]["json"]["text"]["body"]


def test_meta_error_explained(meta):
    meta.fail_for = {"Sarah"}
    add("Sarah Klein", 60, product="Bed Linen 200TC", market="EU")
    out = daily_graph.invoke({"today": TODAY})
    s = out["sent"][0]
    assert s["message_sent"] is False
    assert "131047" in s["error"] and "24h window" in s["error"]


def test_no_token_sends_nothing(meta, monkeypatch):
    monkeypatch.setattr(config, "WA_ACCESS_TOKEN", "")
    add("Ahmed Al-Rashid", 7)
    out = daily_graph.invoke({"today": TODAY})
    assert meta.calls == [] and "WA_ACCESS_TOKEN" in out["sent"][0]["error"]


def test_network_error_does_not_crash(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("no internet")
    monkeypatch.setattr(sender.httpx, "post", boom)
    add("Ahmed Al-Rashid", 7)
    out = daily_graph.invoke({"today": TODAY})
    assert out["sent"][0]["message_sent"] is False and "network error" in out["sent"][0]["error"]


def test_max_sends_per_run(meta, monkeypatch):
    monkeypatch.setattr(config, "MAX_SENDS_PER_RUN", 2)
    for i in range(4):
        add(f"Buyer{i}", 7, product=f"Towel {i}")
    daily_graph.invoke({"today": TODAY})
    assert len(meta.calls) == 2


# ---------- Node 5: updater ----------

def test_day7_sent_moves_to_day30(setup, meta):
    b = add("Ahmed Al-Rashid", 7)
    daily_graph.invoke({"today": TODAY})
    r = row(setup, b["buyer_id"])
    assert r["Followups Sent"] == 1
    assert r["Next Followup Day"] == 30
    assert r["Next Followup"] == (TODAY - timedelta(days=7) + timedelta(days=30)).isoformat()
    assert r["Last Contacted"] == TODAY.isoformat() and r["Status"] == "active"
    log = setup.get_all(MESSAGE_LOG)
    assert len(log) == 1 and log[0]["Day Number"] == 7 and log[0]["Action Taken"].startswith("sent")
    assert log[0]["WA Message ID"] == "wamid.1"           # needed for reply matching in Session 4


def test_late_catch_up_keeps_plan_anchored_to_delivery(setup, meta):
    b = add("Ahmed Al-Rashid", 7)
    daily_graph.invoke({"today": TODAY + timedelta(days=3)})      # Day 7 sent 3 days late
    assert row(setup, b["buyer_id"])["Next Followup"] == (TODAY + timedelta(days=23)).isoformat()


def test_day90_completes_sequence(setup, meta):
    b = add("Sarah Klein", 90, product="Bed Linen 200TC", market="EU")
    daily_graph.invoke({"today": TODAY})
    r = row(setup, b["buyer_id"])
    assert r["Status"] == "completed" and r["Next Followup"] == ""


def test_failed_send_keeps_buyer_due_and_logs_failure(setup, meta):
    meta.fail_for = {"Ahmed"}
    b = add("Ahmed Al-Rashid", 7)
    daily_graph.invoke({"today": TODAY})
    r = row(setup, b["buyer_id"])
    assert r["Followups Sent"] == 0 and r["Next Followup Day"] == 7
    assert setup.get_all(MESSAGE_LOG)[0]["Action Taken"].startswith("FAILED")
    # retried next run once Meta accepts again
    meta.fail_for = set()
    daily_graph.invoke({"today": TODAY + timedelta(days=1)})
    assert row(setup, b["buyer_id"])["Followups Sent"] == 1


def test_silence_alert_flow(setup, meta):
    b = add("Khalid Mansoor", 0, product="Bathrobes Terry 380gsm")
    # pretend Day 7 and Day 30 already went out; next is Day 60
    setup.update(QUEUE, "Buyer ID", b["buyer_id"], {
        "Next Followup": (TODAY + timedelta(days=60)).isoformat(), "Next Followup Day": 60,
        "Followups Sent": 2})
    day45 = TODAY + timedelta(days=45)
    out = daily_graph.invoke({"today": day45})

    assert len(meta.calls) == 2                                    # buyer msg + alert to Junaid
    assert "silence re-engagement" in meta.calls[0]["json"]["text"]["body"]
    assert "Buyer at risk: Khalid Mansoor" in meta.calls[1]["json"]["text"]["body"]
    assert out["sent"][0]["alert_sent"] is True

    r = row(setup, b["buyer_id"])
    assert r["Silence Alerted"] == "yes" and r["Status"] == "active"
    assert r["Next Followup Day"] == 60 and r["Followups Sent"] == 2   # decision A: plan unchanged
    silent = setup.get_all(SILENT)
    assert len(silent) == 1 and silent[0]["Status"] == "at risk" and silent[0]["Days Silent"] == 45
    assert setup.get_all(MESSAGE_LOG)[0]["Day Number"] == "silence"


# ---------- full run ----------

def test_demo_run_sends_three_and_second_run_sends_nothing(setup, meta):
    add("Ahmed Al-Rashid", 7)
    add("Mohammed Hassan", 30, product="Hand Towels 400gsm")
    add("Sarah Klein", 60, product="Bed Linen 200TC", market="EU")
    out = daily_graph.invoke({"today": TODAY})
    assert sum(s["message_sent"] for s in out["sent"]) == 3
    assert out["sheets_updated"] == 3 and len(setup.get_all(MESSAGE_LOG)) == 3

    again = daily_graph.invoke({"today": TODAY})                   # accidental double run
    assert again["sent"] == [] and len(meta.calls) == 3


def test_run_daily_endpoint(setup, meta, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "k")
    import main
    c = TestClient(main.app)
    add("Ahmed Al-Rashid", 0)                                      # due in 7 days, use "today" override
    h = {"X-API-Key": "k"}
    assert c.post("/run-daily", json={}).status_code == 401

    dry = c.post("/run-daily", json={"today": str(TODAY + timedelta(days=7)), "dry_run": True}, headers=h).json()
    assert dry["would_send"] == 1 and meta.calls == []

    live = c.post("/run-daily", json={"today": str(TODAY + timedelta(days=7))}, headers=h).json()
    assert live["sent"] == 1 and live["failed"] == 0 and live["sheets_updated"] == 1
    assert live["results"][0]["wa_message_id"] == "wamid.1"


def test_no_silence_right_after_a_touchpoint(setup, meta):
    # Sarah: delivered 60 days ago, never replied → Day 60 goes out today
    b = add("Sarah Klein", 60, product="Bed Linen 200TC", market="EU")
    daily_graph.invoke({"today": TODAY})
    for d in range(1, 7):                                          # next 6 days: nothing
        assert daily_graph.invoke({"today": TODAY + timedelta(days=d)})["sent"] == [], d
    # day 7 after Day 60 (Day 90 still 23 days away) → silence may fire now
    out = daily_graph.invoke({"today": TODAY + timedelta(days=7)})
    assert [s["message_type"] for s in out["sent"]] == ["silence"]
    assert row(setup, b["buyer_id"])["Next Followup Day"] == 90


def test_no_silence_when_next_touchpoint_is_close(setup, meta):
    b = add("Khalid Mansoor", 0, product="Bathrobes Terry 380gsm")
    setup.update(QUEUE, "Buyer ID", b["buyer_id"], {
        "Next Followup": (TODAY + timedelta(days=60)).isoformat(), "Next Followup Day": 60})
    assert daily_graph.invoke({"today": TODAY + timedelta(days=55)})["sent"] == []   # Day 60 in 5 days
