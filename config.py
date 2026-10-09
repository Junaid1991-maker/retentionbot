"""Central settings for RetentionBot. Everything comes from .env."""
import os

from dotenv import load_dotenv

load_dotenv()

# Google Sheets (leave GOOGLE_SHEETS_ID empty to use the local JSON store for testing)
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "").strip()
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "./credentials.json").strip()
# On Render, Secret Files live in /etc/secrets/ — use it automatically if the local path is missing
if not os.path.exists(GOOGLE_CREDENTIALS_PATH) and os.path.exists("/etc/secrets/credentials.json"):
    GOOGLE_CREDENTIALS_PATH = "/etc/secrets/credentials.json"

# Dashboard login (Streamlit). Empty = no password (local only).
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
LOCAL_STORE_PATH = os.getenv("LOCAL_STORE_PATH", "./local_store.json").strip()

# Retention rules
FOLLOWUP_DAYS = [7, 30, 60, 90]
SILENCE_THRESHOLD_DAYS = int(os.getenv("SILENCE_THRESHOLD_DAYS", "45"))
MIN_GAP_DAYS = int(os.getenv("MIN_GAP_DAYS", "7"))  # min days between any two messages to one buyer

# Groq (Node 3 message generator, Node 6 classifier)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

# WhatsApp Cloud API (Node 4) — same token + number as all other agents
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN", "").strip()
WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID", "1231512983369543").strip()
WA_API_VERSION = os.getenv("WA_API_VERSION", "v23.0").strip()
ALERT_WA_NUMBER = os.getenv("ALERT_WA_NUMBER", "+923183355708").strip()

# Reply handling (Session 4): send a short automatic answer to every buyer reply
AUTO_REPLY = os.getenv("AUTO_REPLY", "true").strip().lower() == "true"

# Daily run safety
SEND_DELAY_SECONDS = float(os.getenv("SEND_DELAY_SECONDS", "3"))   # pause between WhatsApp sends
MAX_SENDS_PER_RUN = int(os.getenv("MAX_SENDS_PER_RUN", "50"))      # runaway guard

# API security (same pattern as NurtureBot: X-API-Key header)
API_KEY = os.getenv("API_KEY", "").strip()

# Demo
DEMO_MODE = os.getenv("DEMO_MODE", "true").strip().lower() == "true"
DEMO_WA_NUMBER = os.getenv("DEMO_WA_NUMBER", "+923183355708").strip()
# In demo mode every message goes to your phone, so add a small header saying who it was for
DEMO_HEADER = os.getenv("DEMO_HEADER", "true").strip().lower() == "true"
