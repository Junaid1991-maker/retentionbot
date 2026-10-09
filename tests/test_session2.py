"""Session 2 tests — Node 3 (message generator). Run: pytest -v

Groq is replaced by a fake, so these run offline and cost nothing.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import config
import sheets
from graph import intake_graph, preview_graph
from nodes import generator as gen
from sheets import LocalStore

TODAY = date(2026, 10, 7)

AHMED = dict(buyer_id="RB-0001", buyer_name="Ahmed Al-Rashid", company="Dubai Laundry Services LLC",
             phone="+923183355708", product="Bath Towels 500gsm White", quantity=500,
             order_value_usd=1525, market="Gulf", followup_day=7)
SARAH = dict(buyer_id="RB-0003", buyer_name="Sarah Klein", company="Berlin Linen GmbH",
             phone="+923183355708", product="Bed Linen 200TC", quantity=200,
             order_value_usd=3000, market="EU", followup_day=60)


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    sheets.set_store(LocalStore(str(tmp_path / "store.json")))
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    yield
    sheets.set_store(None)


def fake_llm(replies):
    """Return a fake _call_llm that hands out replies in order and records prompts."""
    calls = []

    def _fake(messages):
        calls.append(messages)
        r = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    _fake.calls = calls
    return _fake


# ---------- templates & helpers ----------

@pytest.mark.parametrize("mtype", ["day7", "day30", "day60", "day90", "silence"])
@pytest.mark.parametrize("buyer", [AHMED, SARAH])
def test_every_template_fills_and_passes_guardrails(mtype, buyer):
    msg = gen.template_message(buyer, mtype)
    assert gen.check_message(msg, buyer) is None, msg


def test_gulf_uses_bhai_eu_does_not():
    assert "Ahmed bhai" in gen.template_message(AHMED, "day7")
    eu = gen.template_message(SARAH, "day7")
    assert "bhai" not in eu.lower() and "Dear Sarah" in eu


@pytest.mark.parametrize("product,cat", [
    ("Bath Towels 500gsm White", "towels"), ("Terry Bathrobes", "towels"),
    ("Bed Linen 200TC", "home_textiles"), ("Duvet Covers", "home_textiles"),
    ("Organic Cotton T-Shirts", "garments"), ("Denim Jeans", "garments"),
    ("Canvas Tarpaulin", "general"),
])
def test_product_category(product, cat):
    assert gen.product_category(product) == cat


def test_value_tiers():
    assert gen.value_tier(7800) == "high"
    assert gen.value_tier(1525) == "standard"
    assert gen.value_tier(850) == "low"


@pytest.mark.parametrize("mtype,nxt,final", [("day7", 30, False), ("day30", 60, False),
                                             ("day60", 90, False), ("day90", None, True)])
def test_next_followup_day(monkeypatch, mtype, nxt, final):
    monkeypatch.setattr(gen, "_call_llm", fake_llm(["Salam Ahmed bhai, hope all is well."]))
    out = gen.generate_message(AHMED, mtype)
    assert out["next_followup_day"] == nxt and out["is_final_followup"] is final


def test_silence_keeps_pending_touchpoint(monkeypatch):
    # decision A: silence message doesn't move the sequence
    monkeypatch.setattr(gen, "_call_llm", fake_llm(["Sarah, hope all is well on your side."]))
    out = gen.generate_message(SARAH, "silence")
    assert out["is_silence_alert"] is True
    assert out["next_followup_day"] == 60 and out["followup_day"] is None


# ---------- guardrails ----------

@pytest.mark.parametrize("bad,reason", [
    ("Dear Sarah bhai, hope all is well.", "bhai"),
    ("Dear Sarah, our price is USD 4.50 per set.", "price"),
    ("Dear Sarah, get 10% off this week!", "price"),
    ("Dear Sarah, as discussed, here is the offer.", "banned"),
    ("Dear [Name], hope all is well.", "name missing"),
    ("Dear Sarah, " + "very long " * 80, "too long"),
    ("", "empty"),
])
def test_guardrails_catch_bad_messages(bad, reason):
    assert reason in gen.check_message(bad, SARAH)


# ---------- generator flow ----------

def test_good_ai_message_used(monkeypatch):
    fake = fake_llm(["Salam Ahmed bhai,\nHope the towels reached Dubai in perfect shape. Sab theek? 🙏"])
    monkeypatch.setattr(gen, "_call_llm", fake)
    out = gen.generate_message(AHMED, "day7")
    assert out["source"] == "groq" and "Ahmed bhai" in out["personalized_message"]
    assert len(fake.calls) == 1


def test_bad_ai_message_retried_then_fixed(monkeypatch):
    fake = fake_llm(["Dear Sarah bhai, hello", "Dear Sarah, I hope the bed linen arrived well."])
    monkeypatch.setattr(gen, "_call_llm", fake)
    out = gen.generate_message(SARAH, "day7")
    assert out["source"] == "groq" and "bhai" not in out["personalized_message"]
    assert len(fake.calls) == 2
    assert "bhai" in fake.calls[1][-1]["content"]  # retry told the model what went wrong


def test_bad_twice_falls_back_to_template(monkeypatch):
    monkeypatch.setattr(gen, "_call_llm", fake_llm(["10% off!", "10% off!"]))
    out = gen.generate_message(SARAH, "day60")
    assert out["source"] == "template"
    assert out["personalized_message"] == gen.template_message(SARAH, "day60")


def test_groq_down_falls_back_to_template(monkeypatch):
    monkeypatch.setattr(gen, "_call_llm", fake_llm([ConnectionError("no internet")]))
    out = gen.generate_message(AHMED, "day30")
    assert out["source"] == "template" and "groq error" in out["note"]


def test_no_api_key_uses_template_without_calling(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    fake = fake_llm(["should not be used"])
    monkeypatch.setattr(gen, "_call_llm", fake)
    out = gen.generate_message(AHMED, "day7")
    assert out["source"] == "template" and fake.calls == []


def test_prompt_contains_rules_and_facts():
    p = gen.build_prompt(SARAH, "day60")[1]["content"]
    assert "Sarah" in p and "Bed Linen 200TC" in p
    assert "never 'bhai'" in p                      # EU tone
    assert "thread count" in p                     # home textiles rule
    assert "Karachi Terry Mills" in p              # allowed facts


# ---------- graph + API ----------

def test_daily_graph_writes_messages_for_demo_buyers(monkeypatch):
    monkeypatch.setattr(gen, "_call_llm", fake_llm([ConnectionError("offline")]))
    for days, name, product, market in [(7, "Ahmed", "Bath Towels", "Gulf"),
                                        (30, "Mohammed", "Hand Towels", "Gulf"),
                                        (60, "Sarah", "Bed Linen 200TC", "EU")]:
        intake_graph.invoke({"today": TODAY, "order": dict(
            buyer_name=name, phone="+923183355708", product=product, quantity=100,
            order_value_usd=1000, market=market, delivery_date=TODAY - timedelta(days=days))})
    out = preview_graph.invoke({"today": TODAY})
    assert [m["message_type"] for m in out["messages"]] == ["day7", "day30", "day60"]
    assert all(m["personalized_message"] for m in out["messages"])


def test_retain_endpoint(monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "k")
    monkeypatch.setattr(gen, "_call_llm", fake_llm(["Dear Sarah, shall I check availability for you?"]))
    import main
    c = TestClient(main.app)
    body = {**SARAH, "message_type": "day60"}
    assert c.post("/retain", json=body).status_code == 401
    r = c.post("/retain", json=body, headers={"X-API-Key": "k"}).json()
    assert r["personalized_message"].startswith("Dear Sarah")
    assert r["next_followup_day"] == 90 and r["is_final_followup"] is False
    assert c.post("/retain", json={**body, "message_type": "day45"}, headers={"X-API-Key": "k"}).status_code == 422


@pytest.mark.parametrize("product,u", [("Bed Linen 200TC", "sets"), ("Duvet Cover Set", "sets"),
                                       ("Bath Towels 500gsm", "pcs"), ("T-Shirts", "pcs")])
def test_units(product, u):
    assert gen.unit(product) == u


KHALID = dict(buyer_name="Khalid Mansoor", product="Bathrobes Terry 380gsm", quantity=150,
              order_value_usd=850, market="Gulf", followup_day=60)


def test_heavy_urdu_rejected():
    # the real Groq output from Junaid's first live test
    heavy = ("Khalid bhai,\nSalam, ummeed hai sab theek chal raha hai.\nBas yaad dilana tha ke hum abhi bhi "
             "aapke Bathrobes Terry 380gsm ke liye ready hain-agar koi naya requirement ya spec ho, batadein.")
    assert "Urdu" in gen.check_message(heavy, KHALID)


@pytest.mark.parametrize("ok", [
    "Salam Ahmed bhai,\nHope your Bath Towels 500 gsm White arrived safely and the quality is theek. Koi issue toh nahi?\nIf anything needs attention, just let us know - we're here.",
    "Hey Ahmed bhai,\nHope the 400 gsm hand towels are serving you well. Our new 600 gsm range is in stock.\nAgar interest ho, let me know.",
])
def test_light_urdu_allowed(ok):
    assert gen.check_message(ok, AHMED) is None


def test_inshallah_promise_rejected():
    assert "promise" in gen.check_message(
        "Khalid bhai, hope all is well. InshaAllah winter season ke liye sab set ho jayega.", KHALID)


def test_clean_unicode():
    assert gen.clean("“T‑Shirts heads‑up 200”") == "T-Shirts heads-up 200"


def test_all_gulf_templates_pass_urdu_check():
    for m in ["day7", "day30", "day60", "day90", "silence"]:
        assert gen.check_message(gen.template_message(KHALID, m), KHALID) is None
