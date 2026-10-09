"""NODE 3 — Message Generator.

For each buyer due today (or flagged silent), write one WhatsApp message:
  1. Load the template for that touchpoint (sequences/dayN.txt / silence.txt),
     Gulf variant or default (formal English) variant.
  2. Ask Groq to personalise it using the buyer profile + the rules below,
     allowed to use ONLY facts from sequences/sender_profile.json.
  3. Check the result (guardrails). If it fails, retry once.
  4. If Groq is down, not configured, or fails twice → fill the template
     directly. The daily run never breaks because of the AI.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

import config

SEQ_DIR = Path(__file__).resolve().parent.parent / "sequences"
NEXT_DAY = {7: 30, 30: 60, 60: 90, 90: None}
MAX_CHARS = 600

# Phrases that would be wrong for a buyer who may never have replied (decision A),
# or that promise things the bot must never promise on its own.
BANNED = [
    "as discussed", "as we discussed", "thanks for your reply", "thank you for your reply",
    "as you mentioned", "following our conversation", "discount", "free shipping",
    "limited time", "last chance", "urgent",
]
# Common Roman Urdu words — used to keep Gulf messages "mostly English, light Urdu touch".
URDU_WORDS = {
    "hai", "hain", "ke", "ka", "ki", "ko", "se", "aap", "aapka", "aapke", "aapki", "hum", "humare",
    "abhi", "bhi", "tha", "thi", "ho", "sab", "theek", "toh", "nahi", "ya", "liye", "agar", "aur",
    "mein", "koi", "kya", "batadein", "bataen", "dilana", "chal", "raha", "rahi", "jayega", "ummeed",
    "yaad", "bas", "chahiye", "zaroor", "acha", "accha", "shukriya", "jaldi", "naya", "nayi", "wala",
}
MAX_URDU_SHARE = 0.25
PRICE_PATTERN = re.compile(r"(\$|usd|pkr|aed|sar|eur|€|£)\s?\d|\d+\s?%|\d+\s?(usd|dollars|euros?)", re.I)


# ---------- helpers ----------

@lru_cache(maxsize=1)
def _profile() -> dict:
    return json.loads((SEQ_DIR / "sender_profile.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def _template(message_type: str) -> dict:
    raw = (SEQ_DIR / f"{message_type}.txt").read_text(encoding="utf-8")
    goal = raw.split("\n", 1)[0].replace("GOAL:", "").strip()
    gulf = raw.split("--- gulf ---", 1)[1].split("--- default ---", 1)[0].strip()
    default = raw.split("--- default ---", 1)[1].strip()
    return {"goal": goal, "gulf": gulf, "default": default}


def product_category(product: str) -> str:
    p = product.lower()
    if any(k in p for k in ["towel", "terry", "bathrobe", "bath mat"]):
        return "towels"
    if any(k in p for k in ["bed", "linen", "sheet", "duvet", "pillow"]) or re.search(r"\d+\s?tc\b", p):
        return "home_textiles"
    if any(k in p for k in ["garment", "shirt", "tee", "denim", "jean", "hoodie", "apparel", "polo", "trouser"]):
        return "garments"
    return "general"


def value_tier(order_value_usd: float) -> str:
    if order_value_usd > 5000:
        return "high"
    if order_value_usd < 1000:
        return "low"
    return "standard"


def unit(product: str) -> str:
    """Bed linen / duvet sets are counted in sets, everything else in pieces."""
    p = product.lower()
    return "sets" if ("set" in p or product_category(product) == "home_textiles") else "pcs"


def first_name(full: str) -> str:
    return (full or "").strip().split(" ")[0] or "there"


def is_gulf(market: str) -> bool:
    return (market or "").strip().lower() == "gulf"


def _fill(text: str, buyer: dict) -> str:
    cat = product_category(buyer["product"])
    prof = _profile()
    return text.format(
        name=first_name(buyer["buyer_name"]),
        product=buyer["product"],
        quantity=f"{buyer['quantity']:,} {unit(buyer['product'])}",
        update=prof["updates"][cat][0],
        season=prof["seasonal_reasons"].get(buyer.get("market", "Other"), prof["seasonal_reasons"]["Other"]),
    )


def template_message(buyer: dict, message_type: str) -> str:
    t = _template(message_type)
    return _fill(t["gulf"] if is_gulf(buyer.get("market", "")) else t["default"], buyer)


def check_message(msg: str, buyer: dict) -> str | None:
    """Return a reason string if the message breaks a rule, else None."""
    low = msg.lower()
    if not msg.strip():
        return "empty"
    if len(msg) > MAX_CHARS:
        return f"too long ({len(msg)} chars)"
    if first_name(buyer["buyer_name"]).lower() not in low:
        return "buyer name missing"
    if re.search(r"[\[\]{}]", msg):
        return "unfilled placeholder"
    if not is_gulf(buyer.get("market", "")) and "bhai" in low:
        return "'bhai' used for non-Gulf buyer"
    if re.search(r"inshallah|insha allah|inshaallah", low) and re.search(r"(ho jayega|guarantee|will be ready)", low):
        return "sounds like a promise"
    words = re.findall(r"[a-z]+", low)
    if words and sum(w in URDU_WORDS for w in words) / len(words) > MAX_URDU_SHARE:
        return "too much Roman Urdu (keep it mostly English)"
    if PRICE_PATTERN.search(msg):
        return "mentions a price/percentage"
    for b in BANNED:
        if b in low:
            return f"banned phrase: {b}"
    return None


# ---------- prompt ----------

SYSTEM = """You write short WhatsApp follow-up messages from a Pakistani textile exporter to an existing B2B buyer.
Rules:
- Warm, relationship-focused, human. Never pushy, never salesy, no hype words, no exclamation spam.
- Keep the structure and intent of the BASE MESSAGE; personalise it, don't replace it.
- 2–4 short lines, under 450 characters. At most one emoji.
- Use ONLY the facts given. Never invent prices, discounts, certifications, dates or promises.
- Never mention price numbers or percentages.
- Never imply the buyer replied to earlier messages (they may not have).
- Output ONLY the message text. No quotes, no labels, no explanations."""

TONE = {
    "gulf": "Gulf buyer: warm and friendly. MOSTLY ENGLISH (at least 80% of the words). Add only ONE or TWO short Roman Urdu phrases, like the base message does (e.g. 'Salam', 'Koi issue toh nahi?'). Never write whole sentences in Urdu. Address as '<first name> bhai'.",
    "default": "European/other buyer: polite, professional, slightly formal English only. No Urdu words, never 'bhai'. Address by first name.",
}
CATEGORY_HINT = {
    "towels": "Towels/terry: you may reference GSM, hotel/laundry use, hotel season or Ramadan procurement.",
    "home_textiles": "Home textiles/bed linen: you may reference thread count, hotel/retail use, sustainability/DPP readiness for EU.",
    "garments": "Garments: you may reference the season, new collection, lead time availability.",
    "general": "General textile buyer: keep references to quality, specs and availability.",
}
VALUE_HINT = {
    "high": "High-value buyer (> USD 5,000): more personal, owner-to-owner tone.",
    "standard": "Standard buyer: friendly and clear.",
    "low": "Smaller buyer (< USD 1,000): efficient, value-focused, keep it brief.",
}


def build_prompt(buyer: dict, message_type: str) -> list[dict]:
    t = _template(message_type)
    gulf = is_gulf(buyer.get("market", ""))
    cat = product_category(buyer["product"])
    prof = _profile()
    base = template_message(buyer, message_type)
    user = f"""TOUCHPOINT: {message_type} — {t['goal']}
TONE: {TONE['gulf' if gulf else 'default']}
PRODUCT RULE: {CATEGORY_HINT[cat]}
VALUE RULE: {VALUE_HINT[value_tier(float(buyer['order_value_usd']))]}

BUYER:
- First name: {first_name(buyer['buyer_name'])}
- Company: {buyer.get('company', '')}
- Product ordered: {buyer['product']} ({buyer['quantity']:,} {unit(buyer['product'])})
- Market: {buyer.get('market', 'Other')}
- Delivered on: {buyer.get('delivery_date', '')}

ALLOWED FACTS (use at most one, only if it fits this touchpoint):
- Our company: {prof['company']}
- Updates: {'; '.join(prof['updates'][cat])}
- Seasonal reason: {prof['seasonal_reasons'].get(buyer.get('market', 'Other'), prof['seasonal_reasons']['Other'])}

BASE MESSAGE:
{base}

Write the final message."""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


_client = None


def _call_llm(messages: list[dict]) -> str:
    global _client
    if _client is None:
        from groq import Groq
        _client = Groq(api_key=config.GROQ_API_KEY, timeout=30)
    resp = _client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=messages,
        temperature=0.7,
        max_completion_tokens=1500,   # gpt-oss reasons first; leave room
        reasoning_effort="low",
    )
    return clean((resp.choices[0].message.content or ""))


def clean(text: str) -> str:
    """Swap fancy unicode dashes/spaces for plain ones so every phone shows them."""
    for bad, good in {"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u202f": " ", "\u00a0": " ",
                      "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'}.items():
        text = text.replace(bad, good)
    return text.strip().strip('"').strip()


# ---------- main ----------

def generate_message(buyer: dict, message_type: str) -> dict:
    """Generate one message. Always returns a usable message."""
    day = int(message_type.replace("day", "")) if message_type.startswith("day") else None
    is_silence = message_type == "silence"
    out = {
        "buyer_id": buyer.get("buyer_id"),
        "buyer_name": buyer["buyer_name"],
        "phone": buyer.get("phone"),
        "company": buyer.get("company", ""),
        "product": buyer["product"],
        "delivery_date": buyer.get("delivery_date", ""),
        "days_since_last_reply": buyer.get("days_since_last_reply"),
        "message_type": message_type,
        "followup_day": day,
        # silence doesn't move the sequence: next touchpoint stays where it was (decision A)
        "next_followup_day": buyer.get("followup_day") if is_silence else NEXT_DAY.get(day),
        "is_final_followup": day == 90,
        "is_silence_alert": is_silence,
    }

    problem = "GROQ_API_KEY not set"
    if config.GROQ_API_KEY:
        prompt = build_prompt(buyer, message_type)
        for attempt in range(2):
            try:
                msg = _call_llm(prompt)
            except Exception as e:  # network, rate limit, bad key…
                problem = f"groq error: {type(e).__name__}: {e}"[:200]
                break
            problem = check_message(msg, buyer)
            if problem is None:
                return {**out, "personalized_message": msg, "source": "groq", "note": ""}
            prompt = prompt + [
                {"role": "assistant", "content": msg},
                {"role": "user", "content": f"That breaks a rule ({problem}). Rewrite it following all rules."},
            ]

    return {**out, "personalized_message": template_message(buyer, message_type),
            "source": "template", "note": problem}


def message_generator(state: dict) -> dict:
    """LangGraph node: one message per due buyer + per silence alert."""
    queue = state.get("due_buyers", []) + state.get("silence_alerts", [])
    return {"messages": [generate_message(b, b["message_type"]) for b in queue]}
