"""Session 5 tests — dashboard data + dashboard renders + deploy files sane."""
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

import config
import sheets
from graph import daily_graph, intake_graph, reply_graph
from nodes import sender
from sheets import MESSAGE_LOG, LocalStore
from streamlit_app.data import active_rows, log_rows, metrics

ROOT = Path(__file__).resolve().parent.parent
TODAY = date.today()


class FakeResp:
    status_code, content, text = 200, b"x", ""

    def __init__(self, n):
        self.n = n

    def json(self):
        return {"messages": [{"id": f"wamid.{self.n}"}]}


@pytest.fixture
def demo_store(tmp_path, monkeypatch):
    path = tmp_path / "store.json"
    store = LocalStore(str(path))
    sheets.set_store(store)
    for k, v in {"GROQ_API_KEY": "", "WA_ACCESS_TOKEN": "t", "DEMO_MODE": True,
                 "SEND_DELAY_SECONDS": 0, "AUTO_REPLY": True}.items():
        monkeypatch.setattr(config, k, v)
    calls = []
    monkeypatch.setattr(sender.httpx, "post",
                        lambda url, json, headers, timeout: (calls.append(1), FakeResp(len(calls)))[1])
    for name, days, product, market in [("Ahmed Al-Rashid", 7, "Bath Towels 500gsm White", "Gulf"),
                                        ("Mohammed Hassan", 30, "Hand Towels 400gsm", "Gulf"),
                                        ("Sarah Klein", 60, "Bed Linen 200TC", "EU")]:
        intake_graph.invoke({"today": TODAY, "order": dict(
            buyer_name=name, company=f"{name.split()[0]} Co", phone="+923183355708", product=product,
            quantity=500, order_value_usd=1525, market=market, delivery_date=TODAY - timedelta(days=days))})
    daily_graph.invoke({"today": TODAY})
    ahmed = next(r for r in store.get_all(MESSAGE_LOG) if r["Name"].startswith("Ahmed"))
    reply_graph.invoke({"phone": "", "text": "need same order again", "msg_id": "",
                        "context_id": ahmed["WA Message ID"], "today": TODAY})
    yield store, path
    sheets.set_store(None)


def all_tabs(store):
    return [store.get_all(t) for t in (sheets.QUEUE, sheets.MESSAGE_LOG, sheets.REORDERS, sheets.SILENT)]


def test_metrics_after_demo(demo_store):
    m = metrics(*all_tabs(demo_store[0]), today=TODAY)
    assert m["active"] == 2              # Ahmed moved to reorder
    assert m["messages_today"] == 3
    assert m["reorders"] == 1
    assert m["reply_rate"] == 33         # 1 reply / 3 sent
    assert m["repeat_value_usd"] == 1525


def test_metrics_empty():
    m = metrics([], [], [], [])
    assert m == {"active": 0, "messages_today": 0, "reorders": 0, "at_risk": 0, "reply_rate": 0,
                 "repeat_value_usd": 0, "opted_out": 0, "total_buyers": 0}


def test_active_rows_sorted_and_formatted(demo_store):
    rows = active_rows(demo_store[0].get_all(sheets.QUEUE))
    assert [r["Buyer"] for r in rows] == ["Mohammed Hassan", "Sarah Klein"]
    assert rows[0]["Sent"] == "1/4" and rows[0]["Next"].startswith("Day 60")
    assert rows[0]["Order USD"] == "1,525"


def test_log_rows_newest_first_and_trimmed(demo_store):
    rows = log_rows(demo_store[0].get_all(sheets.MESSAGE_LOG))
    assert rows[-1]["Buyer"] == "Ahmed Al-Rashid"
    assert rows[-1]["Class"] == "REORDER" and rows[-1]["Reply"] == "need same order again"
    assert all(len(r["Message"]) <= 91 for r in rows)


def test_dashboard_renders(demo_store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    at = AppTest.from_file(str(ROOT / "streamlit_app" / "app.py"), default_timeout=30).run()
    assert not at.exception
    labels = {m.label: m.value for m in at.metric}
    assert labels["Reorders"] == "1" and labels["Active buyers"] == "2"
    assert len(at.tabs) == 4


def test_dashboard_password_gate(demo_store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "secret")
    at = AppTest.from_file(str(ROOT / "streamlit_app" / "app.py"), default_timeout=30).run()
    assert len(at.metric) == 0                      # nothing visible before login
    at.text_input[0].input("wrong").run()
    assert at.error and len(at.metric) == 0
    at.text_input[0].input("secret").run()
    assert len(at.metric) == 6


def test_render_blueprint_valid():
    bp = yaml.safe_load((ROOT / "render.yaml").read_text())
    names = {s["name"]: s for s in bp["services"]}
    assert set(names) == {"retentionbot-api", "retentionbot-dashboard"}
    api_keys = {e["key"] for e in names["retentionbot-api"]["envVars"]}
    assert {"GOOGLE_SHEETS_ID", "API_KEY", "GROQ_API_KEY", "WA_ACCESS_TOKEN"} <= api_keys
    assert "$PORT" in names["retentionbot-api"]["startCommand"]
    assert "$PORT" in names["retentionbot-dashboard"]["startCommand"]
    secrets = [e for e in names["retentionbot-api"]["envVars"]
               if e["key"] in ("API_KEY", "GROQ_API_KEY", "WA_ACCESS_TOKEN")]
    assert all(e.get("sync") is False and "value" not in e for e in secrets)   # never in git


@pytest.mark.parametrize("wf", ["retentionbot-daily.yml", "keep-awake.yml"])
def test_workflows_valid(wf):
    d = yaml.safe_load((ROOT / ".github" / "workflows" / wf).read_text())
    assert d[True]["schedule"][0]["cron"]   # yaml parses "on:" as True


def test_gitignore_protects_secrets():
    ignored = (ROOT / ".gitignore").read_text().split()
    for f in (".env", "credentials.json", "local_store.json", "render_secrets.txt"):
        assert f in ignored
