# 0010. Agent runtime: Postgres-backed workflow queue, worker process, LangGraph, LLM router

- Status: accepted
- Date: 2026-10-13

## Context
Phase 2 runs the first agent. Model calls take seconds, providers rate-limit and fail, and FR-005/FR-013 require idempotent workflow creation, bounded retries with backoff, and dead-lettering. The spec's queue is SQS (Phase 5/7), which isn't available locally yet.

## Decision
- **API only records; a worker executes.** `POST /v1/workflows` stores the request (idempotent on `(org_id, Idempotency-Key)` with a body fingerprint) and returns 202. `python -m agentflow.worker` runs it.
- **Postgres is the queue for now.** Workflows carry `status`, `attempts`, `available_at` (backoff) and `locked_until` (lease). Claiming is one `SECURITY DEFINER` function using `FOR UPDATE SKIP LOCKED`: the worker's only cross-tenant capability. Everything after the claim runs under RLS with the workflow's org as tenant context.
- **Failure handling.** Any exception → a failed step, `available_at = now + exponential backoff with jitter`, up to `workflow_max_attempts`, then `failed` (dead letter) with an audit event. An expired lease is reclaimed; a worker that lost its lease can't overwrite the new attempt's result.
- **Short transactions.** Claim, each step and the outcome are separate small transactions. None spans a model call.
- **LangGraph without a checkpointer yet.** Nodes are pure (no DB/HTTP); the worker persists each node's output as a `workflow_steps` row. Durable checkpoints arrive with pause/resume for approvals (Phase 6).
- **LLM router (`llm.py`).** Agents request a `TaskType` + Pydantic schema. The router tries the task's models in order (fallback on rate limit / outage), uses provider-enforced JSON schema plus Pydantic validation, makes one repair attempt, and logs model/tokens/latency. Default: `gpt-oss-20b` → `gpt-oss-120b` (both strict-schema on Groq, low reasoning effort for classification).
- **Agents = prompt + schema + deterministic rules.** The model's triage answer passes `apply_rules()` (e.g. fraud ≥ high and always human review) before anything uses it.
- **Evals.** `make eval` runs a golden set against the real model; safety-tagged cases must all pass and accuracy must be ≥ 85%. Not in CI yet (needs a key and spends tokens).

## Consequences
- One less moving part locally; the queue interface (`claim_next`) is small enough to swap for SQS in Phase 7.
- Polling adds up to 1 s latency per idle worker. Acceptable; SQS long-polling replaces it.
- Postgres as a queue fits our volume (thousands/day). At much higher rates the claim query and table churn need measuring.
- Fallback models share one provider: a Groq outage still stops triage until Bedrock is added.

## Alternatives considered
- Run agents inside the request: simplest, but a crash loses the work and requests hold connections for seconds.
- FastAPI BackgroundTasks: same process, no durability or retries.
- Celery/RQ: another broker and framework to learn and run. Postgres already gives us transactions and locking.
- LangGraph Postgres checkpointer now: real value only once graphs pause for humans (Phase 6).
