# 0013. Customer chat, failed-transfer reversal, card freeze, insights

- Status: accepted
- Date: 2026-10-21

## Context
Target users are banks and microfinance institutions serving Gen Z customers, who prefer texting (and voice notes) to calling or visiting a branch. They ask general questions ("how do I issue a cheque?") and report problems ("I sent 5,000, it was debited but never arrived"; "payments I didn't make"). Until now only staff could submit requests, and there was no tool for transfers or fraud containment.

## Decision
- **Chat is a channel, not a second brain.** Each customer message creates an ordinary workflow (`channel: chat`): the same triage, RAG, research, resolution, policy, approvals, tools and audit. The worker posts the outcome back into the chat (`conversations.service.post_outcome`) when the workflow pauses (a deterministic "with an officer" message), completes (the communication agent's reply) or fails.
- **Cards come from facts, not prose.** Each assistant message carries a `card` built by code from the workflow state (refund reference, transfer trace, frozen card, case number, sources), so the UI never shows a number the ledger didn't produce.
- **Context:** the last 6 turns are prepended to the workflow message, so follow-ups ("how long is it valid?") resolve.
- **Customer auth:** a customer token (JWT with a separate audience, 24h) bound to one conversation. Staff mint it (`POST /v1/conversations`); in production the bank's app would, after its own login. Staff and customer tokens are mutually rejected. The frontend keeps the token in the URL fragment, then in sessionStorage, and removes it from the address bar.
- **Human takeover:** a staff reply switches the conversation to `human` (agents stay quiet) until it is handed back.
- **Failed transfers:** transfers carry the network's `transfer_status` (credited/pending/failed). A reversal validates only for a network-confirmed `failed` transfer; the policy rule `failed_transfer_reversal` allows it without maker-checker (the money is owed back). Blocks such as the daily reversal cap still apply. Pending past 24h → escalation → inquiry ticket.
- **Card freeze:** new action `freeze_card` (fraud only, active card only), policy `protective_freeze` = allow: it is protective and reversible. The fraud ticket still opens.
- **Voice notes:** Groq Whisper (`whisper-large-v3-turbo`) transcribes, and the text enters the same pipeline.
- **Insights:** `/v1/insights` computes the AI resolution rate, money returned, median time to resolve, cards frozen and AI cost (tokens × list price) on read.
- `chat_messages` is append-only (no UPDATE grant); the app may update only `ledger_accounts.card_status` (column-level grant).

## Consequences
- Verified with real models: a Roman Urdu failed-transfer report gets reversed in about 25 s with a Roman Urdu reply; a cheque question is answered from the help guide (Urdu script answered in Urdu); fraud freezes the card and opens TCK-…; a stuck transfer opens an inquiry case; small duplicates are refunded; large ones go to review.
- Latency is 15–35 s (6 sequential model calls). Acceptable behind a typing indicator; streaming and a fast path for FAQs come next.
- Chat is polled (1.5 s). WebSockets or SSE would replace polling at scale.

## Alternatives considered
- A separate chat bot with its own LLM loop: faster to demo, but it would bypass the policy engine, approvals and audit.
- Customer accounts with passwords in AgentFlow: the bank already authenticates its customers, so a bank-issued token is the realistic integration.
