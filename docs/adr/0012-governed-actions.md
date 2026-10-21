# 0012. Governed actions: resolution agent, policy engine, approvals, exactly-once tools

- Status: accepted
- Date: 2026-10-21

## Context
The product promise (proposal §2) is that AI does the investigation while deterministic software decides whether money moves, with human approval above limits and an audit trail regulators accept. FR-009/010/011 require permissioned tools, approvals for high-risk actions and safe resumption; spec 02 §7 requires idempotent side effects.

## Decision
- **Research (code):** a read-only ledger tool gathers the customer's last 30 days of transactions; **duplicates are detected by code** (same amount, currency and merchant within 10 minutes), not by the model.
- **Resolution agent (LLM):** proposes one typed action (`refund_transaction` / `escalate_to_human` / `no_action`). **Code validates it** against the evidence: the transaction must exist and be a posted debit, the amount must be within 0 and the debit amount, a duplicate refund must target the later debit of a detected pair, and fraud is never refunded automatically. An invalid proposal becomes an escalation.
- **Policy engine (code, `policy/engine.py`):** allow / require_approval / block with rule ids. Blocks (amount over 100,000; 3+ reversals in 24h; unknown transaction) beat approvals (amount over 2,000 = maker-checker; fraud; triage flagged for review; no verified policy answer).
- **Approvals:** a `waiting_approval` workflow status and an `approvals` row (24h TTL). Admins decide; **maker-checker**: the submitter can't approve their own request. Deciding re-queues the workflow at stage `execution`. Expiry is a `SECURITY DEFINER` sweep run by the worker.
- **Two graphs instead of a checkpointer:** analysis (… → research → resolve → policy_gate) and execution (execute → communicate). The analysis result is persisted on the workflow; the execution stage resumes from it. No worker waits for humans.
- **Refund tool (`tools/refund.py`):** re-authorizes at execution time (policy re-evaluated on fresh ledger data, plus the approval) → idempotency key `sha256(workflow + tool + target + version)` in `action_executions` → refund with a row lock → verification in the ledger → audit. These all commit in one transaction.
- **Communication agent:** drafts the customer reply from the *outcome* (never the proposal).

## Consequences
- Verified end to end with real models: the PKR 1,200 duplicate is refunded automatically; the PKR 5,000 duplicate pauses, maker-checker blocks the submitter, a second admin approves, the workflow resumes and refunds, and the ledger is verified.
- Exactly-once is atomic only because the ledger lives in our database. A real payment API needs the key sent as the provider's Idempotency-Key, with an `in_progress` row written before the call and reconciliation afterwards.
- Policy limits are code defaults for all orgs; a per-org `policies` table (versioned, spec 02 §4) is the next step.
- Replies are drafts; sending needs the email tool (Phase 5).
- LangGraph's Postgres checkpointer would allow mid-graph interrupts; two persisted stages were enough here and keep workers stateless.

## Alternatives considered
- Letting the LLM call tools directly (function calling): faster to build, but the model would hold execution authority, which is the opposite of the core principle.
- One graph with `interrupt()` and a checkpointer: more general, but adds a second persistence mechanism next to our workflow tables.
- Synchronous approval inside the API request: impossible, since approvals take hours.
