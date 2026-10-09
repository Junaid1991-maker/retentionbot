"""RetentionBot API — port 8008.

Run:  uvicorn main:app --reload --host 0.0.0.0 --port 8008

Endpoints:
  GET  /ping, /health                         (no key)
  POST /add-buyer      Node 1                 (Session 1)
  POST /due-today      Node 2                 (Session 1)
  POST /retain         Node 3, one buyer      (Session 2)
  POST /preview-today  Node 2 + 3, no sending (Session 2)
  POST /run-daily      Node 2 -> 3 -> 4 -> 5  (Session 3)  ← the 9:30 AM job calls this
  POST /check-buyer    is this phone / reply-to one of our buyers?   (Session 4)
  POST /classify-reply Node 6 only, no side effects               (Session 4)
  POST /incoming       raw Meta webhook JSON → full reply handling (Session 4)
                       ← n8n Order Agent workflow calls this; returns handled true/false
All POSTs need header X-API-Key (same as NurtureBot).
"""
from datetime import date
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

import config
from graph import daily_graph, intake_graph, preview_graph, reply_graph, schedule_graph
from nodes.classifier import classify_reply
from nodes.reply_handler import already_seen, find_buyer, parse_meta
from nodes.generator import generate_message
from nodes.intake import DeliveredOrder
from sheets import MESSAGE_LOG, QUEUE, REORDERS, get_store

app = FastAPI(title="RetentionBot", version="0.4.0-session4")


def require_key(x_api_key: str = Header(default="")):
    if config.API_KEY and x_api_key != config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@app.get("/ping")
def ping():
    return {"pong": True}


@app.get("/health")
def health():
    store = get_store()
    queue = store.get_all(QUEUE)
    today = date.today().isoformat()
    return {
        "status": "running",
        "active_buyers": sum(r["Status"] == "active" for r in queue),
        "messages_today": sum(str(r["Date"])[:10] == today for r in store.get_all(MESSAGE_LOG)),
        "reorders_total": len(store.get_all(REORDERS)),
        "silent_buyers": sum(r["Silence Alerted"] == "yes" or r["Status"] == "silent" for r in queue),
        "storage": "google_sheets" if config.GOOGLE_SHEETS_ID else "local_json",
        "groq": "configured" if config.GROQ_API_KEY else "missing (template fallback)",
        "whatsapp": "configured" if config.WA_ACCESS_TOKEN else "missing",
        "demo_mode": config.DEMO_MODE,
    }


@app.post("/add-buyer", dependencies=[Depends(require_key)])
def add_buyer(order: DeliveredOrder):
    out = intake_graph.invoke({"order": order.model_dump()})
    if out.get("error"):
        raise HTTPException(status_code=400, detail=out["error"])
    b = out["buyer"]
    return {
        "buyer_id": b["buyer_id"],
        "added": not out.get("duplicate", False),
        "duplicate": out.get("duplicate", False),
        "first_followup": b["next_followup_date"],
        "followup_day": b["followup_day"],
        "status": b["status"],
    }


class DateRequest(BaseModel):
    today: Optional[date] = None  # override for testing ("what if today were…")


def _today(body: Optional[DateRequest]) -> date:
    return (body.today if body and body.today else None) or date.today()


@app.post("/due-today", dependencies=[Depends(require_key)])
def due_today(body: Optional[DateRequest] = None):
    today = _today(body)
    out = schedule_graph.invoke({"today": today})
    return {
        "date": today.isoformat(),
        "due_count": len(out["due_buyers"]),
        "silence_count": len(out["silence_alerts"]),
        "due_buyers": out["due_buyers"],
        "silence_alerts": out["silence_alerts"],
    }


class RetainRequest(BaseModel):
    buyer_id: str = ""
    buyer_name: str = Field(min_length=1)
    company: str = ""
    phone: str = ""
    product: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    order_value_usd: float = Field(gt=0)
    market: str = "Other"
    delivery_date: str = ""
    message_type: Literal["day7", "day30", "day60", "day90", "silence"]
    followup_day: Optional[int] = None  # for silence: the touchpoint still pending


@app.post("/retain", dependencies=[Depends(require_key)])
def retain(req: RetainRequest):
    return generate_message(req.model_dump(), req.message_type)


@app.post("/preview-today", dependencies=[Depends(require_key)])
def preview_today(body: Optional[DateRequest] = None):
    """Write today's messages WITHOUT sending or changing the Sheet."""
    today = _today(body)
    out = preview_graph.invoke({"today": today})
    return {"date": today.isoformat(), "count": len(out["messages"]), "messages": out["messages"]}


class RunRequest(BaseModel):
    today: Optional[date] = None
    dry_run: bool = False


@app.post("/run-daily", dependencies=[Depends(require_key)])
def run_daily(body: Optional[RunRequest] = None):
    """The daily job: write → send on WhatsApp → update Sheets. Safe to call twice
    (anyone already messaged today is no longer due)."""
    body = body or RunRequest()
    today = body.today or date.today()
    if body.dry_run:
        out = preview_graph.invoke({"today": today})
        return {"date": today.isoformat(), "dry_run": True, "would_send": len(out["messages"]),
                "messages": out["messages"]}
    out = daily_graph.invoke({"today": today})
    sent = out.get("sent", [])
    return {
        "date": today.isoformat(),
        "dry_run": False,
        "demo_mode": config.DEMO_MODE,
        "sent": sum(s["message_sent"] for s in sent),
        "failed": sum(not s["message_sent"] for s in sent),
        "silence_alerts": sum(s["is_silence_alert"] and s["message_sent"] for s in sent),
        "sheets_updated": out.get("sheets_updated", 0),
        "results": [{k: s.get(k) for k in ("buyer_id", "buyer_name", "message_type", "message_sent",
                                           "sent_to", "source", "error", "wa_message_id")} for s in sent],
    }


class CheckBuyerRequest(BaseModel):
    phone: str = ""
    context_id: str = ""   # id of the message the buyer replied to (reply-to)


@app.post("/check-buyer", dependencies=[Depends(require_key)])
def check_buyer(req: CheckBuyerRequest):
    found = find_buyer(req.phone, req.context_id)
    if not found:
        return {"is_retention_buyer": False, "buyer_id": "", "buyer_name": "", "last_order": ""}
    r = found["row"]
    return {"is_retention_buyer": True, "buyer_id": r["Buyer ID"], "buyer_name": r["Name"],
            "last_order": f"{r['Quantity']} x {r['Product']} (USD {r['Order Value USD']}) delivered {r['Delivery Date']}"}


class ClassifyRequest(BaseModel):
    reply_text: str
    buyer_id: str = ""
    followup_day: Optional[int] = None


@app.post("/classify-reply", dependencies=[Depends(require_key)])
def classify(req: ClassifyRequest):
    """Classification only — changes nothing, sends nothing."""
    ctx = f"Day {req.followup_day} follow-up" if req.followup_day else ""
    return classify_reply(req.reply_text, ctx)


@app.post("/incoming", dependencies=[Depends(require_key)])
def incoming(payload: dict):
    """Raw Meta webhook body. handled=false → n8n passes it on to the Order Agent.
    handled=true + handoff_to_order_agent=true → n8n ALSO runs the Order Agent with order_context."""
    msg = parse_meta(payload)
    if not msg:
        return {"handled": False, "reason": "not a text message"}
    if already_seen(msg["msg_id"]):
        return {"handled": True, "reason": "duplicate webhook ignored", "handoff_to_order_agent": False}
    out = reply_graph.invoke(msg)
    if not out.get("handled"):
        return {"handled": False, "reason": out.get("reason", "")}
    res = out["result"]
    return {
        "handled": True,
        "buyer_id": out["row"]["Buyer ID"],
        "buyer_name": out["row"]["Name"],
        "classification": res["classification"],
        "confidence": res["confidence"],
        "reasoning": res["reasoning"],
        "source": res["source"],
        "handoff_to_order_agent": out.get("handoff_to_order_agent", False),
        "order_context": out.get("order_context"),
        "auto_reply_sent": out.get("auto_reply_sent", False),
        "alert_sent": out.get("alert_sent", False),
    }
