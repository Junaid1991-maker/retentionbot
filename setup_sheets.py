"""Create the 4 tabs + header rows in the RetentionBot Google Sheet.

Before running:
  1. Create an empty Google Sheet named "RetentionBot".
  2. Share it (Editor) with the service-account email inside credentials.json
     (the "client_email" field — same one NurtureBot's sheet is shared with).
  3. Put the sheet ID in .env as GOOGLE_SHEETS_ID.
Then:  python setup_sheets.py   (safe to re-run; it only rewrites header rows)
"""
import config
from sheets import TABS, LocalStore, SheetStore


def main():
    if not config.GOOGLE_SHEETS_ID:
        LocalStore()
        print(f"GOOGLE_SHEETS_ID empty -> using local store {config.LOCAL_STORE_PATH}")
        return

    store = SheetStore()
    existing = {ws.title: ws for ws in store.book.worksheets()}
    for tab, headers in TABS.items():
        ws = existing.get(tab) or store.book.add_worksheet(title=tab, rows=1000, cols=len(headers))
        if ws.col_count < len(headers):          # new columns added in a later session
            ws.add_cols(len(headers) - ws.col_count)
        ws.update(range_name="A1", values=[headers])
        ws.freeze(rows=1)
        print(f"✅ {tab}: {len(headers)} columns")

    default = existing.get("Sheet1")
    if default and not any(default.get_all_values()):
        store.book.del_worksheet(default)
        print("Removed empty Sheet1")


if __name__ == "__main__":
    main()
