# RetentionBot — AI WhatsApp customer-retention agent for textile exporters

Stage 7 of the Sales Automation Engine by **M. Junaid Iqbal (Automiq)**.

A buyer orders, the goods are delivered… then silence, and three months later they reorder from a competitor.
RetentionBot follows up automatically on WhatsApp at **Day 7, 30, 60 and 90** after delivery, writes every
message with AI in the right tone for the buyer's market, and turns replies into action:

| Buyer replies | RetentionBot does |
|---|---|
| "need same order again" | 🔥 alerts the exporter + hands the full order to the WhatsApp Order Agent → proforma PDF in seconds |
| "what's the price now?" | polite holding reply + 💬 alert (a human answers prices) |
| "stop" | opts out immediately (rules always win over AI) |
| "thanks bhai" | short warm reply, sequence continues |
| *nothing for 45+ days* | re-engagement message + ⚠️ "buyer at risk" alert |

## Stack
Python 3.11 · LangGraph · FastAPI · Groq (openai/gpt-oss-120b) · Meta WhatsApp Cloud API · Google Sheets (gspread) ·
Streamlit · n8n · Render · GitHub Actions · pytest (125 tests)

## Architecture
```
Delivered order ─► Node 1 Intake ─► Google Sheet (Retention Queue)
9:30 AM daily  ─► Node 2 Scheduler ─► Node 3 AI Generator (guardrails) ─► Node 4 WhatsApp Sender ─► Node 5 Sheets Updater
Buyer reply    ─► n8n router (LeadHunter → NurtureBot → RetentionBot → Order Agent) ─► Node 6 Classifier ─► alert / opt-out / hand-off
```

## AI guardrails
Messages are rejected and rewritten (or replaced by a safe template) if they: mention prices or discounts,
invent facts not in `sequences/sender_profile.json`, use "bhai" for non-Gulf buyers, are mostly Roman Urdu,
make promises ("InshaAllah… ho jayega"), exceed 600 characters, or imply the buyer replied earlier.

## Run locally
```
pip install -r requirements.txt
cp .env.example .env            # fill in keys
python setup_sheets.py           # create the 4 Sheet tabs
python reset_demo.py             # 3 demo buyers due today
python run_daily.py --dry-run    # preview
python run_daily.py              # send (DEMO_MODE routes everything to one test phone)
python simulate_reply.py Ahmed "need same order again"
uvicorn main:app --port 8008     # API
streamlit run streamlit_app/app.py
pytest
```

## Endpoints (X-API-Key on all POSTs)
`/ping` `/health` `/add-buyer` `/due-today` `/retain` `/preview-today` `/run-daily` `/check-buyer` `/classify-reply` `/incoming`

## Deploy
`render.yaml` (Render Blueprint: API + dashboard) · `.github/workflows/retentionbot-daily.yml` (9:30 AM PKT run) ·
`.github/workflows/keep-awake.yml` (no cold starts during working hours).
