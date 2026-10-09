"""Storage layer for the 4-tab RetentionBot Google Sheet.

Two stores with the same 3 methods (get_all / append / update):
  - SheetStore -> real Google Sheet via gspread (used when GOOGLE_SHEETS_ID is set)
  - LocalStore -> local JSON file (used for offline testing / pytest)
Nodes never care which one they get.
"""
import json
from pathlib import Path

import config

QUEUE = "Retention Queue"
MESSAGE_LOG = "Message Log"
REORDERS = "Reorders"
SILENT = "Silent Buyers"

TABS = {
    QUEUE: [
        "Buyer ID", "Name", "Company", "Phone", "Product", "Quantity",
        "Order Value USD", "Delivery Date", "Market", "Last Contacted",
        "Last Reply", "Followups Sent", "Next Followup", "Next Followup Day",
        "Days Since Last Reply", "Status", "Silence Alerted",
    ],
    MESSAGE_LOG: [
        "Date", "Buyer ID", "Name", "Day Number", "Message Type", "Message Sent",
        "Reply Received", "Reply Text", "Reply Classification", "Action Taken",
        "WA Message ID",
    ],
    REORDERS: [
        "Date", "Buyer ID", "Name", "Company", "Phone", "Original Order",
        "Reply Text", "Handed to Order Agent", "New Order Value", "Notes",
    ],
    SILENT: [
        "Buyer ID", "Name", "Company", "Phone", "Last Contact", "Days Silent",
        "Re-engagement Sent", "Response", "Status",
    ],
}


class LocalStore:
    def __init__(self, path: str | None = None):
        self.path = Path(path or config.LOCAL_STORE_PATH)
        if not self.path.exists():
            self._save({tab: [] for tab in TABS})

    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_all(self, tab: str) -> list[dict]:
        return self._load().get(tab, [])

    def append(self, tab: str, row: dict) -> None:
        data = self._load()
        data.setdefault(tab, []).append({h: row.get(h, "") for h in TABS[tab]})
        self._save(data)

    def update(self, tab: str, key_col: str, key, fields: dict) -> bool:
        data = self._load()
        for row in data.get(tab, []):
            if str(row.get(key_col)) == str(key):
                row.update(fields)
                self._save(data)
                return True
        return False


    def clear(self, tab: str) -> None:
        data = self._load()
        data[tab] = []
        self._save(data)


class SheetStore:
    def __init__(self):
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_PATH,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self.book = gspread.authorize(creds).open_by_key(config.GOOGLE_SHEETS_ID)

    def _ws(self, tab: str):
        return self.book.worksheet(tab)

    def get_all(self, tab: str) -> list[dict]:
        # numericise_ignore=["all"] keeps "+923..." phones and IDs as text
        return self._ws(tab).get_all_records(numericise_ignore=["all"])

    def append(self, tab: str, row: dict) -> None:
        self._ws(tab).append_row(
            [row.get(h, "") for h in TABS[tab]], value_input_option="RAW"
        )

    def update(self, tab: str, key_col: str, key, fields: dict) -> bool:
        from gspread.utils import rowcol_to_a1

        ws = self._ws(tab)
        headers = TABS[tab]
        keys = ws.col_values(headers.index(key_col) + 1)
        if str(key) not in keys:
            return False
        row_num = keys.index(str(key)) + 1
        ws.batch_update(
            [
                {"range": rowcol_to_a1(row_num, headers.index(k) + 1), "values": [[v]]}
                for k, v in fields.items()
            ],
            value_input_option="RAW",
        )
        return True


    def clear(self, tab: str) -> None:
        """Delete all data rows, keep the header row."""
        ws = self._ws(tab)
        if ws.row_count > 1:
            ws.batch_clear([f"A2:{chr(64 + len(TABS[tab]))}{ws.row_count}"])


_store = None


def get_store():
    """Return the shared store. Google Sheet if configured, else local JSON."""
    global _store
    if _store is None:
        _store = SheetStore() if config.GOOGLE_SHEETS_ID else LocalStore()
    return _store


def set_store(store) -> None:
    """Used by tests to inject a temporary LocalStore."""
    global _store
    _store = store
