# 0012. Governed actions: resolution agent, policy engine, approvals, exactly-once tools

- Status: accepted
- Date: 2026-10-21

## Context
The product promise (proposal §2) is that AI does the investigation while deterministic software decides whether money moves, with human approval above limits and an audit trail regulators accept. FR-009/010/011 require permissioned tools, approvals for high-risk actions and safe resumption; spec 02 §7 requires idempotent side effects.

## Decision
- **Research (code):** a read-only ledger tool gathers the customer's last 30 days of transactions; **duplicates are detected by code** (same amount, currency and merchant within 10 minutes), not by the model.
- **Resolution agent (LLM):** proposes one typed action (`refund_transaction` / `escalate_to_human` / `no_action`). **Code validates it** against the evidence: the transaction must exist and be a posted debit, the amount must be within 0 and the debit amount, a duplicate refund must target the later debit of a detected pair, and fraud is never refunded automatically. An invalid proposal becomes an escalation.
- **Policy engine (code, `policy/engine.py`):** allow / require_approval / block with rule ids. Blocks (amount over the tenant's configured ceiling; configured daily reversal count reached; unknown transaction) beat approvals (amount over the tenant's auto-approval threshold; fraud; triage flagged for review; no verified policy answer). Defaults are 100,000 maximum, 3 reversals per day, and 2,000 auto-approval threshold in the demo currency.
- **Approvals:** a `waiting_approval` workflow status and an `approvals` row (24h TTL). Admins decide; **maker-checker**: the submitter can't approve their own request. Deciding re-queues the workflow at stage `execution`. Expiry is a `SECURITY DEFINER` sweep run by the worker.
- **Two graphs instead of a checkpointer:** analysis (… → research → resolve → policy_gate) and execution (execute → communicate). The analysis result is persisted on the workflow; the execution stage resumes from it. No worker waits for humans.
- **Refund tool (`tools/refund.py`):** re-authorizes at execution time (policy re-evaluated on fresh ledger data, plus the approval) → idempotency key `sha256(workflow + tool + target + version)` in `action_executions` → write through the `MoneyActions` adapter → verification → audit. In the simulator these commit in one database transaction.
- **Tenant policy limits:** admins can set the auto-approval amount, automated refund ceiling, and daily reversal count in Settings. Values are stored per organization, versioned, validated by the database and API, and audit logged. Existing tenants without a row use the safe defaults.
- **Audit integrity:** events are linked by a per-tenant SHA-256 hash chain and an admin endpoint verifies the chain. This makes edits/deletions detectable, but a database superuser who can rewrite the full chain can still erase evidence; production tamper resistance needs an external WORM store or signed external checkpoints.
- **Approval expiry:** the worker atomically expires overdue approvals and re-queues waiting workflows, then records an `approval.expired` audit event. Expired actions are re-authorized and are not executed.
- **Communication agent:** drafts the customer reply from the *outcome* (never the proposal).

## Consequences
- Verified end to end with real models: the PKR 1,200 duplicate is refunded automatically; the PKR 5,000 duplicate pauses, maker-checker blocks the submitter, a second admin approves, the workflow resumes and refunds, and the ledger is verified.
- The bundled `DatabaseLedgerAdapter` still only changes AgentFlow's simulated ledger. The `MoneyActions` protocol is the write-side integration seam; it does not claim to connect to a bank. A live adapter must forward the stable key to the provider, persist an `in_progress` operation before making the request, and reconcile ambiguous timeouts against provider state before retrying. No external provider is selected in this repository.
- Exactly-once is an internal database guarantee in the simulator. It is not an exactly-once guarantee across an external provider boundary.
- Replies are drafts; sending needs the email tool (Phase 5).
- LangGraph's Postgres checkpointer would allow mid-graph interrupts; two persisted stages were enough here and keep workers stateless.

## Alternatives considered
- Letting the LLM call tools directly (function calling): faster to build, but the model would hold execution authority, which is the opposite of the core principle.
- One graph with `interrupt()` and a checkpointer: more general, but adds a second persistence mechanism next to our workflow tables.
- Synchronous approval inside the API request: impossible, since approvals take hours.

## Production boundaries

- Provider writes are intentionally not enabled without a selected bank/payment API, its idempotency contract, and its reconciliation/read-after-timeout endpoint. Do not describe the simulator as a live banking integration.
- The hash chain is tamper-evident against an isolated row edit or deletion; it is not cryptographically immutable against a database superuser. Export chain heads to an independently administered immutable store to make privileged rewriting detectable.
