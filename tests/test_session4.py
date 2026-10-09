"""Session 4 tests — Node 6 (classifier) + reply handling + /incoming.

Groq and Meta are faked.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import config
import sheets
from graph import daily_graph, intake_graph, reply_graph
from nodes import classifier as clf
from nodes import reply_handler as rh
from nodes import sender
from sheets import MESSAGE_LOG, QUEUE, REORDERS, SILENT, LocalStore

TODAY = date(2026, 10, 7)


class FakeResp:
    status_code = 200
    content = b"x"
    text = ""

    def __init__(self, n):
        self.n = n

    def json(self):
        return {"messages": [{"id": f"wamid.{self.n}"}]}


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    store = LocalStore(str(tmp_path / "store.json"))
    sheets.set_store(store)
    rh._seen.clear()
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "WA_ACCESS_TOKEN", "t")
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "AUTO_REPLY", True)
    monkeypatch.setattr(config, "SEND_DELAY_SECONDS", 0)
    yield store
    sheets.set_store(None)


@pytest.fixture
def wa(monkeypatch):
    sent = []

    def fake_post(url, json, headers, timeout):
        sent.append(json)
        return FakeResp(len(sent))
    monkeypatch.setattr(sender.httpx, "post", fake_post)
    return sent


def demo_three(store):
    """3 demo buyers, all messaged today → Message Log has wamid.1/2/3."""
    for name, days, product, market in [("Ahmed Al-Rashid", 7, "Bath Towels 500gsm White", "Gulf"),
                                        ("Mohammed Hassan", 30, "Hand Towels 400gsm", "Gulf"),
                                        ("Sarah Klein", 60, "Bed Linen 200TC", "EU")]:
        intake_graph.invoke({"today": TODAY, "order": dict(
            buyer_name=name, company=f"{name.split()[0]} Co", phone="+923183355708", product=product,
            quantity=500, order_value_usd=1525, market=market, delivery_date=TODAY - timedelta(days=days))})
    daily_graph.invoke({"today": TODAY})
    return {r["Name"].split()[0]: r["WA Message ID"] for r in store.get_all(MESSAGE_LOG)}


def reply(context_id, text, phone="923183355708"):
    return reply_graph.invoke({"phone": phone, "text": text, "msg_id": "", "context_id": context_id,
                               "today": TODAY})


def queue_row(store, name):
    return next(r for r in store.get_all(QUEUE) if r["Name"].startswith(name))


# ---------- classifier ----------

@pytest.mark.parametrize("text,label", [
    ("need same order again", "REORDER"),
    ("Send proforma please", "REORDER"),
    ("1000 pcs this time", "REORDER"),
    ("same order dobara bhej do", "REORDER"),
    ("what is the price now?", "INQUIRY"),
    ("any new designs available", "INQUIRY"),
    ("rate kya hai", "INQUIRY"),
    ("STOP", "STOP"),
    ("please stop sending these", "STOP"),
    ("not interested, remove me", "STOP"),
    ("message mat bhejo", "STOP"),
    ("thanks, all good", "NEUTRAL"),
    ("Shukriya bhai", "NEUTRAL"),
])
def test_rules_fallback(text, label):
    assert clf.classify_reply(text)["classification"] == label


def test_stop_word_inside_sentence_is_not_opt_out():
    assert clf.classify_reply("towels arrived, no stop at customs, thanks")["classification"] != "STOP"


def test_stop_rules_beat_ai(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(clf, "_call_llm", lambda t, c: '{"classification":"REORDER","confidence":0.9}')
    assert clf.classify_reply("stop")["classification"] == "STOP"


def test_ai_used_when_valid(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(clf, "_call_llm", lambda t, c:
                        'sure: {"classification": "REORDER", "confidence": 0.93, "reasoning": "wants repeat"}')
    r = clf.classify_reply("haan bhai, wahi wala maal chahiye")
    assert r["classification"] == "REORDER" and r["source"] == "groq" and r["confidence"] == 0.93


def test_unsure_ai_stop_is_ignored(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(clf, "_call_llm", lambda t, c: '{"classification":"STOP","confidence":0.5}')
    assert clf.classify_reply("hmm not now maybe later")["classification"] == "NEUTRAL"


@pytest.mark.parametrize("raw", ["not json", '{"classification": "MAYBE"}', ""])
def test_bad_ai_answer_falls_back_to_rules(monkeypatch, raw):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(clf, "_call_llm", lambda t, c: raw)
    r = clf.classify_reply("need same order again")
    assert r["classification"] == "REORDER" and r["source"] == "rules"


def test_ai_error_falls_back(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")

    def boom(t, c):
        raise TimeoutError()
    monkeypatch.setattr(clf, "_call_llm", boom)
    assert clf.classify_reply("price?")["classification"] == "INQUIRY"


# ---------- matching ----------

def test_parse_meta_text_and_status():
    body = {"entry": [{"changes": [{"value": {"messages": [{
        "from": "923183355708", "id": "wamid.IN1", "type": "text", "text": {"body": "hi"},
        "context": {"id": "wamid.1"}}]}}]}]}
    assert rh.parse_meta(body) == {"phone": "923183355708", "text": "hi", "msg_id": "wamid.IN1",
                                   "context_id": "wamid.1"}
    status = {"entry": [{"changes": [{"value": {"statuses": [{"status": "read"}]}}]}]}
    assert rh.parse_meta(status) is None
    image = {"entry": [{"changes": [{"value": {"messages": [{"from": "1", "type": "image"}]}}]}]}
    assert rh.parse_meta(image) is None


def test_demo_mode_needs_reply_to(setup, wa):
    demo_three(setup)
    assert reply("", "need same order again")["handled"] is False          # plain message → Order Agent
    assert reply("wamid.unknown", "need same order again")["handled"] is False


def test_live_mode_matches_by_phone(setup, wa, monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    intake_graph.invoke({"today": TODAY, "order": dict(
        buyer_name="Omar Saeed", phone="+971 50 111 2222", product="Bath Towels", quantity=100,
        order_value_usd=900, market="Gulf", delivery_date=TODAY)})
    out = reply("", "thanks bhai", phone="971501112222")
    assert out["handled"] and out["row"]["Name"] == "Omar Saeed"
    assert reply("", "hello", phone="971509999999")["handled"] is False


# ---------- actions ----------

def test_reorder_flow(setup, wa):
    ids = demo_three(setup)
    n_before = len(wa)
    out = reply(ids["Ahmed"], "need same order again")

    assert out["result"]["classification"] == "REORDER"
    assert out["handoff_to_order_agent"] is True
    assert out["order_context"]["quantity"] == 500 and out["order_context"]["product"] == "Bath Towels 500gsm White"
    assert out["order_context"]["order_message"] == "500 pcs Bath Towels 500gsm White, same as last order"

    r = queue_row(setup, "Ahmed")
    assert r["Status"] == "reorder" and r["Last Reply"] == TODAY.isoformat()
    ro = setup.get_all(REORDERS)
    assert len(ro) == 1 and ro[0]["Handed to Order Agent"] == "Y" and "Day 7" in ro[0]["Notes"]
    log = next(x for x in setup.get_all(MESSAGE_LOG) if x["WA Message ID"] == ids["Ahmed"])
    assert log["Reply Received"] == "yes" and log["Reply Classification"].startswith("REORDER")

    new = wa[n_before:]
    assert len(new) == 2                                                   # ack to buyer + alert to Junaid
    assert "Shukriya Ahmed bhai" in new[0]["text"]["body"]
    assert new[1]["text"]["body"].startswith("🔥 REORDER: Ahmed Al-Rashid")

    # reordered buyer leaves the sequence
    assert all(b["buyer_name"] != "Ahmed Al-Rashid"
               for b in daily_graph.invoke({"today": TODAY + timedelta(days=30)})["sent"])


def test_inquiry_alerts_but_keeps_sequence(setup, wa):
    ids = demo_three(setup)
    out = reply(ids["Sarah"], "what is the price now?")
    assert out["result"]["classification"] == "INQUIRY" and out["handoff_to_order_agent"] is False
    assert queue_row(setup, "Sarah")["Status"] == "active"
    assert wa[-1]["text"]["body"].startswith("💬 Question from Sarah Klein")
    assert "bhai" not in wa[-2]["text"]["body"]                           # EU holding reply


def test_stop_opts_out(setup, wa):
    ids = demo_three(setup)
    reply(ids["Mohammed"], "stop")
    assert queue_row(setup, "Mohammed")["Status"] == "opted_out"
    assert "won't send further updates" in wa[-1]["text"]["body"]
    later = daily_graph.invoke({"today": TODAY + timedelta(days=60)})
    assert all(b["buyer_name"] != "Mohammed Hassan" for b in later["sent"])


def test_neutral_resets_silence_clock(setup, wa):
    ids = demo_three(setup)
    reply(ids["Sarah"], "thank you, all good")
    r = queue_row(setup, "Sarah")
    assert r["Status"] == "active" and r["Last Reply"] == TODAY.isoformat()
    # without the reply Sarah (delivered 80 days ago by then) would get a silence msg on day 20;
    # with it, her silence clock restarted today. (Mohammed, who never replied, does get one.)
    sent = daily_graph.invoke({"today": TODAY + timedelta(days=20)})["sent"]
    assert [s["buyer_name"] for s in sent] == ["Mohammed Hassan"]


def test_silent_buyer_recovered(setup, wa):
    b = intake_graph.invoke({"today": TODAY, "order": dict(
        buyer_name="Khalid Mansoor", phone="+923183355708", product="Bathrobes Terry", quantity=150,
        order_value_usd=850, market="Gulf", delivery_date=TODAY)})["buyer"]
    setup.update(QUEUE, "Buyer ID", b["buyer_id"], {
        "Next Followup": (TODAY + timedelta(days=60)).isoformat(), "Next Followup Day": 60})
    day45 = TODAY + timedelta(days=45)
    daily_graph.invoke({"today": day45})                                   # silence msg goes out
    silence_id = setup.get_all(MESSAGE_LOG)[-1]["WA Message ID"]
    reply(silence_id, "sorry bhai, was travelling. all good")
    assert setup.get_all(SILENT)[0]["Status"] == "recovered"


def test_reply_to_day90_still_handled(setup, wa):
    intake_graph.invoke({"today": TODAY, "order": dict(
        buyer_name="Sarah Klein", phone="+923183355708", product="Bed Linen 200TC", quantity=200,
        order_value_usd=3000, market="EU", delivery_date=TODAY - timedelta(days=90))})
    daily_graph.invoke({"today": TODAY})
    assert queue_row(setup, "Sarah")["Status"] == "completed"
    out = reply(setup.get_all(MESSAGE_LOG)[-1]["WA Message ID"], "yes please send the proforma")
    assert out["result"]["classification"] == "REORDER" and queue_row(setup, "Sarah")["Status"] == "reorder"


def test_auto_reply_can_be_switched_off(setup, wa, monkeypatch):
    ids = demo_three(setup)
    monkeypatch.setattr(config, "AUTO_REPLY", False)
    n = len(wa)
    reply(ids["Ahmed"], "need same order again")
    assert len(wa) == n + 1 and wa[-1]["text"]["body"].startswith("🔥 REORDER")   # alert only


# ---------- API ----------

def meta_body(text, context_id, msg_id="wamid.IN1"):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "messages": [{"from": "923183355708", "id": msg_id, "type": "text",
                      "text": {"body": text}, "context": {"id": context_id}}]}}]}]}


def test_incoming_endpoint(setup, wa, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "k")
    ids = demo_three(setup)
    import main
    c = TestClient(main.app)
    h = {"X-API-Key": "k"}
    assert c.post("/incoming", json=meta_body("x", ids["Ahmed"])).status_code == 401

    r = c.post("/incoming", json=meta_body("need same order again", ids["Ahmed"]), headers=h).json()
    assert r["handled"] and r["classification"] == "REORDER" and r["handoff_to_order_agent"]
    assert r["order_context"]["buyer_name"] == "Ahmed Al-Rashid" and r["alert_sent"]

    dup = c.post("/incoming", json=meta_body("need same order again", ids["Ahmed"]), headers=h).json()
    assert dup["reason"] == "duplicate webhook ignored" and len(setup.get_all(REORDERS)) == 1

    other = c.post("/incoming", json=meta_body("Hi", "", msg_id="wamid.IN2"), headers=h).json()
    assert other["handled"] is False                                       # → Order Agent

    status = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}
    assert c.post("/incoming", json=status, headers=h).json()["handled"] is False


def test_check_buyer_and_classify_endpoints(setup, wa, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "k")
    ids = demo_three(setup)
    import main
    c = TestClient(main.app)
    h = {"X-API-Key": "k"}
    r = c.post("/check-buyer", json={"phone": "923183355708", "context_id": ids["Sarah"]}, headers=h).json()
    assert r["is_retention_buyer"] and r["buyer_name"] == "Sarah Klein"
    assert c.post("/check-buyer", json={"phone": "923183355708"}, headers=h).json()["is_retention_buyer"] is False
    k = c.post("/classify-reply", json={"reply_text": "send proforma", "buyer_id": "RB-0001"}, headers=h).json()
    assert k["classification"] == "REORDER"
    assert queue_row(setup, "Ahmed")["Status"] == "active"                 # classify-only: nothing changed
