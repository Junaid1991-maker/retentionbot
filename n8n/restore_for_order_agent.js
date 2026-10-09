// n8n Code node "Restore for Order Agent" — paste this whole file into the Code node.
// Runs when RetentionBot did NOT fully handle the message (or it was a REORDER).
// 1. Gives the Order Agent the original Meta webhook again (Edit Fields expects it).
// 2. For a REORDER, swaps "need same order again" for the real order line,
//    e.g. "500 pcs Bath Towels 500gsm White, same as last order", so the
//    Order Agent can extract item + quantity + GSM and send the proforma.
const hook = $('Webhook1').first().json;
let r = {};
try { r = $('RetentionBot').first().json || {}; } catch (e) { r = {}; }

if (r.handoff_to_order_agent && r.order_context && r.order_context.order_message) {
  const copy = JSON.parse(JSON.stringify(hook));
  copy.body.entry[0].changes[0].value.messages[0].text.body = r.order_context.order_message;
  return [{ json: copy }];
}
return [{ json: hook }];
