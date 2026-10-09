"""Run the daily RetentionBot job from your laptop.

  python run_daily.py --dry-run   -> show what WOULD be sent (nothing sent, Sheet unchanged)
  python run_daily.py             -> write + send on WhatsApp + update the Sheet

In DEMO_MODE every message goes to DEMO_WA_NUMBER (your phone).
Before a real send: message "Hi" from that phone to the WhatsApp test number
(24-hour rule), or Meta will refuse the messages.
"""
import sys
from datetime import date

import config
from graph import daily_graph, preview_graph


def main():
    today = date.today()
    dry = "--dry-run" in sys.argv
    print(f"RetentionBot daily run · {today} · {'DRY RUN' if dry else 'LIVE'} · "
          f"demo_mode={config.DEMO_MODE} · groq={'on' if config.GROQ_API_KEY else 'off'} · "
          f"whatsapp={'on' if config.WA_ACCESS_TOKEN else 'MISSING'}\n")

    if dry:
        out = preview_graph.invoke({"today": today})
        print(f"Would send {len(out['messages'])} message(s):")
        for m in out["messages"]:
            print(f"  · {m['buyer_name']:<16} {m['message_type']:<8} ({m['source']})")
        return

    if not config.WA_ACCESS_TOKEN:
        print("WA_ACCESS_TOKEN is missing in .env — nothing sent.")
        return

    out = daily_graph.invoke({"today": today})
    sent = out.get("sent", [])
    if not sent:
        print("Nobody is due today. Nothing sent.")
        return
    for s in sent:
        if s["message_sent"]:
            extra = " + ⚠️ alert to you" if s.get("alert_sent") else ""
            print(f"  ✅ {s['buyer_name']:<16} {s['message_type']:<8} → {s['sent_to']}{extra}")
        else:
            print(f"  ❌ {s['buyer_name']:<16} {s['message_type']:<8} {s['error']}")
    ok = sum(s["message_sent"] for s in sent)
    print(f"\nSent {ok}/{len(sent)} · Sheet rows updated: {out.get('sheets_updated', 0)}")
    if ok < len(sent):
        print("Failed buyers stay due and will be retried on the next run.")


if __name__ == "__main__":
    main()
