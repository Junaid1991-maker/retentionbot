"""LangGraph wiring for RetentionBot.

  intake_graph   : delivered order -> Node 1 (Order Intake)
  schedule_graph : Node 2 only (who is due today?) — cheap, no AI calls
  preview_graph  : Node 2 -> Node 3 (who is due + write their messages, send nothing)
  daily_graph    : Node 2 -> 3 -> 4 -> 5 (write, send on WhatsApp, update Sheets)
  reply_graph    : match buyer -> Node 6 (classify) -> act (alert / opt-out / hand-off)
"""
from datetime import date
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from nodes.generator import message_generator
from nodes.intake import order_intake
from nodes.reply_handler import match_buyer, reply_actions, reply_classifier
from nodes.scheduler import schedule_checker
from nodes.sender import whatsapp_sender
from nodes.updater import sheets_updater


class IntakeState(TypedDict, total=False):
    today: date
    order: dict
    buyer: Optional[dict]
    duplicate: bool
    error: Optional[str]


class DailyState(TypedDict, total=False):
    today: date
    due_buyers: list
    silence_alerts: list
    messages: list
    sent: list
    sheets_updated: int


class ReplyState(TypedDict, total=False):
    today: date
    # in
    phone: str
    text: str
    msg_id: str
    context_id: str
    # filled by nodes
    handled: bool
    reason: str
    row: dict
    log_row: Optional[dict]
    result: dict
    handoff_to_order_agent: bool
    order_context: Optional[dict]
    auto_reply_sent: bool
    alert_sent: bool


def build_intake_graph():
    g = StateGraph(IntakeState)
    g.add_node("order_intake", order_intake)
    g.add_edge(START, "order_intake")
    g.add_edge("order_intake", END)
    return g.compile()


def build_schedule_graph():
    g = StateGraph(DailyState)
    g.add_node("schedule_checker", schedule_checker)
    g.add_edge(START, "schedule_checker")
    g.add_edge("schedule_checker", END)
    return g.compile()


def build_preview_graph():
    g = StateGraph(DailyState)
    g.add_node("schedule_checker", schedule_checker)
    g.add_node("message_generator", message_generator)
    g.add_edge(START, "schedule_checker")
    g.add_edge("schedule_checker", "message_generator")
    g.add_edge("message_generator", END)
    return g.compile()


def build_daily_graph():
    g = StateGraph(DailyState)
    g.add_node("schedule_checker", schedule_checker)
    g.add_node("message_generator", message_generator)
    g.add_node("whatsapp_sender", whatsapp_sender)
    g.add_node("sheets_updater", sheets_updater)
    g.add_edge(START, "schedule_checker")
    g.add_edge("schedule_checker", "message_generator")
    g.add_edge("message_generator", "whatsapp_sender")
    g.add_edge("whatsapp_sender", "sheets_updater")
    g.add_edge("sheets_updater", END)
    return g.compile()


def build_reply_graph():
    g = StateGraph(ReplyState)
    g.add_node("match_buyer", match_buyer)
    g.add_node("reply_classifier", reply_classifier)
    g.add_node("reply_actions", reply_actions)
    g.add_edge(START, "match_buyer")
    g.add_conditional_edges("match_buyer", lambda s: "reply_classifier" if s.get("handled") else END)
    g.add_edge("reply_classifier", "reply_actions")
    g.add_edge("reply_actions", END)
    return g.compile()


intake_graph = build_intake_graph()
schedule_graph = build_schedule_graph()
preview_graph = build_preview_graph()
daily_graph = build_daily_graph()
reply_graph = build_reply_graph()
