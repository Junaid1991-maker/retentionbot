"""NODE 6 — Reply Classifier.

Sorts a buyer's WhatsApp reply into one of:
  REORDER  — wants to order again          → alert Junaid + hand off to Order Agent
  INQUIRY  — asking price/stock/products    → friendly holding reply + alert Junaid
  STOP     — wants no more messages         → opt out immediately
  NEUTRAL  — thanks / chit-chat             → short warm reply, sequence continues

Order of decisions (same idea as NurtureBot: "STOP rules always win"):
  1. STOP keyword rules  → STOP, no AI needed, can't be overridden
  2. Groq (JSON answer)  → used if it parses and is a valid label
       AI may only say STOP with confidence >= 0.8 (opt-out is irreversible)
  3. Groq down / bad answer → keyword rules for REORDER / INQUIRY, else NEUTRAL
"""
import json
import re

import config

LABELS = ("REORDER", "INQUIRY", "STOP", "NEUTRAL")

STOP_PATTERNS = [
    # bare "stop" only as the whole message, so "no stop at customs" isn't an opt-out
    r"^\W*stop\W*$", r"\bstop (sending|messag|texting|contacting|these)", r"\bplease stop\b",
    r"\bunsubscribe\b", r"\bremove me\b", r"\bnot interested\b",
    r"\b(don'?t|do not|pls don'?t|please don'?t) (message|msg|contact|text)\b",
    r"\bno more (messages|msgs)\b", r"\bband karo\b", r"\bmat bhej", r"\bmessage mat\b",
]
REORDER_PATTERNS = [
    r"\bre-?order\b", r"\border again\b", r"\bsame order\b", r"\bsame as (last|before)\b",
    r"\bneed more\b", r"\bready to order\b", r"\bplace (an |the )?order\b", r"\bproforma\b",
    r"\bsend (the |a )?pi\b", r"\bnew shipment\b", r"\bdobara\b", r"\bphir se\b",
    r"\b\d[\d,]*\s?(pcs|pieces|sets|units|ctns|cartons|dozen)\b",
]
INQUIRY_PATTERNS = [
    r"\bprice\b", r"\brate\b", r"\bcost\b", r"\bquote\b", r"\bavailab", r"\bin stock\b",
    r"\bnew (products?|designs?|range|items?)\b", r"\bcatalog", r"\bsample", r"\blead time\b",
    r"\bkya (hai|rate)\b", r"\?",
]

ACTIONS = {
    "REORDER": "alert_junaid + log_reorder + handoff_order_agent",
    "INQUIRY": "holding_reply + alert_junaid + continue_sequence",
    "STOP": "opt_out + confirm",
    "NEUTRAL": "warm_reply + continue_sequence",
}


def _match(patterns, text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def rule_label(text: str) -> str | None:
    if _match(STOP_PATTERNS, text):
        return "STOP"
    if _match(REORDER_PATTERNS, text):
        return "REORDER"
    if _match(INQUIRY_PATTERNS, text):
        return "INQUIRY"
    return None


SYSTEM = """You classify a B2B textile buyer's WhatsApp reply to a supplier's follow-up message.
Labels:
- REORDER: wants to buy again (e.g. "need same order again", "send proforma", "1000 pcs this time", "ready to order", "dobara bhej do").
- INQUIRY: asks a question about price, stock, availability, specs, new products, samples, lead time — but has not decided to order.
- STOP: clearly asks to stop messages / not interested / remove from list.
- NEUTRAL: thanks, greetings, confirmations of delivery, small talk, "will let you know".
Replies may be English, Roman Urdu or a mix.
Answer ONLY with JSON: {"classification": "<LABEL>", "confidence": <0..1>, "reasoning": "<one short sentence>"}"""


_client = None


def _call_llm(reply_text: str, context: str) -> str:
    global _client
    if _client is None:
        from groq import Groq
        _client = Groq(api_key=config.GROQ_API_KEY, timeout=20)
    resp = _client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": f"Our last message: {context}\n\nBuyer reply: {reply_text}"}],
        temperature=0,
        max_completion_tokens=800,
        reasoning_effort="low",
    )
    return resp.choices[0].message.content or ""


def _parse(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    label = str(d.get("classification", "")).upper().strip()
    if label not in LABELS:
        return None
    try:
        conf = max(0.0, min(1.0, float(d.get("confidence", 0.7))))
    except (TypeError, ValueError):
        conf = 0.7
    return {"classification": label, "confidence": conf, "reasoning": str(d.get("reasoning", ""))[:200]}


def classify_reply(reply_text: str, context: str = "") -> dict:
    """Return {classification, confidence, reasoning, action, source}."""
    text = (reply_text or "").strip()
    rules = rule_label(text)

    if rules == "STOP":
        out = {"classification": "STOP", "confidence": 1.0,
               "reasoning": "Opt-out keyword found (rules always win).", "source": "rules"}
        return {**out, "action": ACTIONS["STOP"]}

    if config.GROQ_API_KEY and text:
        try:
            ai = _parse(_call_llm(text, context))
        except Exception:
            ai = None
        if ai and not (ai["classification"] == "STOP" and ai["confidence"] < 0.8):
            return {**ai, "source": "groq", "action": ACTIONS[ai["classification"]]}

    label = rules or "NEUTRAL"
    out = {"classification": label, "confidence": 0.6 if rules else 0.5,
           "reasoning": "Keyword rules (AI unavailable or unsure).", "source": "rules"}
    return {**out, "action": ACTIONS[label]}
