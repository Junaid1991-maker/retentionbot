"""Pretend a buyer replied on WhatsApp — no n8n, no ngrok needed.

  python simulate_reply.py Ahmed "need same order again"
  python simulate_reply.py Sarah "what is the price now?"
  python simulate_reply.py Mohammed "stop"
  python simulate_reply.py Ahmed "need same order again" --dry   (classify only, change nothing)

It finds the last message we sent that buyer (Message Log), builds the same
JSON Meta would send for a reply-to, and runs the real reply handling:
Sheet updates + WhatsApp auto-reply + alert to your phone.
"""
import sys

from graph import reply_graph
from nodes.classifier import classify_reply
from sheets import MESSAGE_LOG, get_store

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry"]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    who, text = args[0].lower(), args[1]

    sent = [r for r in get_store().get_all(MESSAGE_LOG)
            if who in r["Name"].lower() and r.get("WA Message ID")]
    if not sent:
        print(f"No sent message found for '{args[0]}' in Message Log. Run run_daily.py first.")
        sys.exit(1)
    last = sent[-1]
    print(f"Buyer reply to: {last['Name']} · Day {last['Day Number']} (id …{last['WA Message ID'][-8:]})")
    print(f"Reply text:     \"{text}\"\n")

    if "--dry" in sys.argv:
        r = classify_reply(text, last["Message Sent"])
        print(f"→ {r['classification']} ({r['source']}, confidence {r['confidence']:.2f})\n  {r['reasoning']}")
        sys.exit(0)

    out = reply_graph.invoke({"phone": "", "text": text, "msg_id": "", "context_id": last["WA Message ID"]})
    if not out.get("handled"):
        print(f"Not handled: {out.get('reason')} (buyer may be opted out / reordered already)")
        sys.exit(0)
    r = out["result"]
    print(f"→ {r['classification']} ({r['source']}, confidence {r['confidence']:.2f})\n  {r['reasoning']}")
    print(f"  auto-reply sent: {out['auto_reply_sent']} · alert to you: {out['alert_sent']} · "
          f"hand to Order Agent: {out['handoff_to_order_agent']}")
