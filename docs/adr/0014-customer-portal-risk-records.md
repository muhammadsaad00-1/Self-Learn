# 0014. Customer portal, criticality analysis, answers from records, case hand-off

- Status: accepted
- Date: 2026-10-22

## Context
Customers and bank staff need separate apps. Staff must be able to pass cases to each other. Critical issues (account takeover, scams) must reliably reach a human, even when the model under-rates them. Many "problems" are customer mistakes the records settle at once (the transfer was delivered, there is only one charge), so a human shouldn't have to look.

## Decision
- **Separate customer app** (`/customer`): its own login (registered mobile number + 6-digit one-time code, `/v1/customer/login/{start,verify}`) and its own session (customer token, sessionStorage), never the staff dashboard. Tabs: Chat, Activity (the same ledger the agents read), Cases (code and status only; internal notes stay with staff).
  - The OTP is stored hashed in Redis for 5 minutes, max 5 tries, 3 sends per 10 minutes. "Start" answers the same way for unknown numbers (no enumeration). Codes are returned in the response outside prod only (no SMS provider yet).
  - The bank comes from the bank's app link (`?bank=<org id>`), so there is no cross-tenant lookup before login.
- **Criticality analysis in code** (`policy/risk.py`): named, scored signals (credentials shared, account takeover, scam, legal threat, distress, fraud category, PKR 50,000+ at stake, repeat contact within 72h, triage critical), combined with triage urgency. Code can **raise** priority, never lower it. Critical → ticket (`ticket_reasons`), ticket priority = assessed priority, signals in the ticket and the decision trail.
- **Answers from records:** new action `answer_from_records` with `record_references`. Code checks that every reference exists in the customer's ledger (else escalate). Policy `records_answer` = allow (read-only). The communication agent receives those rows as facts plus the finding, and **no policy text**: in a real run, the policy text made the model promise a reversal the records disproved. Informational requests from known customers also reach the resolution agent, so "did my salary arrive?" is answered.
- **Case hand-off:** ticket actions `assign` (to a member of the org) and `note`, an `activity` trail on the ticket (also audited), a `mine` filter, and on resolve an optional `customer_message` posted to the customer's chat as a team message that doesn't take the chat over.

## Consequences
- Verified with real models: "my 3000 to Sara didn't arrive" → records show it was delivered → kind answer with the record card, no ticket. "Daraz charged me twice" (only one charge) → "only one debit, reference …", no promise. "I gave the bank officer my OTP" → critical, card frozen, TCK-… "Did my salary come?" → yes, with the record. OTP login works in the browser.
- Keyword signals are English/Roman Urdu regexes: cheap and explainable, but they miss paraphrases. The model's urgency still covers those. Add a signal (and a test) for every miss.
- The resolution agent now runs for informational requests from known customers: one more model call for FAQs.

## Alternatives considered
- Letting the LLM decide criticality alone: it can be argued down by the message ("this is low priority").
- Customer passwords: banks already authenticate customers; OTP mirrors their apps and needs no password storage.
