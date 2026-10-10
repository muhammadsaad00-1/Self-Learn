# AgentFlow — Project Context

Multi-tenant AI business-operations platform for SMBs. Requests come in, get triaged and planned by agents, are grounded in RAG, and lead to proposed actions. Deterministic policy decides whether an action is allowed, blocked, or needs human approval. Governed tools execute it, and everything is audited.

**Core rule: the model proposes, the system decides.** LLM output is never the authority for permissions, tenant boundaries, money, irreversible actions, or workflow state transitions.

## Source of truth
- Original specs: `AgentFlow_Technical_Documentation/*.docx` (do not edit).
- Searchable text copies: `docs/spec/01..05_*.md`. Grep these instead of re-parsing the docx files.
  - 01 product/FRs/NFRs/DoD · 02 architecture, data model, API, idempotency · 03 agents/RAG/eval · 04 AWS/security/CI/CD · 05 roadmap/backlog/rules
- Decisions we make that differ from or add to the spec go in `docs/adr/NNNN-title.md` and get summarized under "Decisions" below.

## Stack (from spec; changes recorded in ADRs)
Python + FastAPI · LangGraph + LangChain · PostgreSQL + pgvector · Redis · S3 · SQS/EventBridge · Bedrock (behind a provider abstraction) · OpenTelemetry · Docker Compose locally · Terraform · GitHub Actions · Angular + TypeScript frontend · Groq (LLM) · fastembed (local embeddings).

## Repo layout
- `backend/`: Python 3.12 / uv project. Package `agentflow` lives in `backend/src/agentflow/`, with one subpackage per domain module as each phase lands. Tests are in `backend/tests/`.
- `frontend/`: Angular app (Phase 1).
- `infra/`: Terraform (Phase 7).
- `docs/spec/`: spec text. `docs/adr/`: architecture decision records.
- `docker-compose.yml`: local stack. `Makefile`: common commands (`make help`).

## Architecture stance
- **Modular monolith first.** One FastAPI app plus one worker process. Domain modules have clear boundaries; split into services only with a measured reason (spec rule).
- Domain modules (spec 02 §3): identity, workflow, knowledge, agent, tooling, policy, approval, automation, audit, evaluation, usage.
- Modules talk to each other only through their public service functions, never by reaching into another module's tables or models.
- Add AWS managed services (Step Functions, EventBridge, AgentCore) only when they solve a concrete problem. Locally, everything runs in Docker Compose.

## Non-negotiable engineering rules (spec 05 §15 + 02 §5)
- Every tenant-owned row has `org_id`. Every repository query is tenant-scoped. Postgres RLS is defense in depth. Cross-tenant tests are required for every sensitive repository.
- Every external mutation accepts an `Idempotency-Key`. Tool side effects use `action_key = sha256(workflow_id + tool + logical_target + action_version)`.
- Agent-to-agent contracts are typed Pydantic. Never parse prose to decide whether to execute.
- Agent loops are bounded: steps, tool calls, tokens, cost, wall-clock, retries, and delegation depth.
- Secrets never go into prompts, logs, code, or images. Retrieved and user content is untrusted data and never enters the system prompt.
- Write tools follow this order: authenticate → authorize tenant/role → validate args → policy → idempotency → execute → typed `ToolResult` → audit/telemetry.
- Run tool authorization again at execution time, even after an approval.

## Commands
`make help` lists all of them. Key ones: `make install` (deps + git hooks) · `make up`/`down` (Docker stack) · `make run` (API with reload) · `make test` · `make check` (everything CI runs) · `make migrate` · `make migration m="..."` (autogenerate, then review: ADR 0006).
uv lives at `~/.local/bin/uv` (official installer; the Intel Homebrew at /usr/local builds from source and is too slow).

## Conventions
- Conventional commits. One feature per branch/PR.
- **Review flow:** write files with the Edit/Write tools (the IDE shows inline diffs). Before every push, show the user the full `git diff` (+/- lines) grouped by concern with a one-line "why" per file, then **wait for approval**. Then push and open the PR with `gh pr create`.
- Every non-trivial logic path ships with a test. Integration tests run against real Postgres (Compose), not mocks.
- Keep this file current: update **Build status** when a feature lands and **Decisions** when an ADR is written.

## Local environment notes (2026-10-06)
- macOS. Docker, Node 22, git, and psql are installed.
- System Python is 3.8, which is too old. Python is managed with `uv` (Python 3.12 is installed by uv).
- `uv` 0.12 and `gh` 2.102 are in `~/.local/bin`. `codex` is not installed.

## Decisions
- **Purpose is learning.** Build the full spec, including every AWS service, IaC, MCP, evals and the frontend. Don't skip components as "not needed yet". Order still follows the roadmap phases.
- **LLM provider: Groq** (free tier), behind the provider abstraction so Bedrock and others can plug in later. The user supplies all API keys. Ask for them; never invent or reuse keys.
- **Never use the Anthropic account/API** anywhere in this project (code, config, CI, defaults).
- Keys live in `.env` (git-ignored) locally and in Secrets Manager on AWS. Never commit them.
- **Embeddings:** a local model (fastembed) behind an embedding-provider interface; move to Bedrock later. Bedrock Titan is pay-per-use, though very cheap.
- **Frontend: Angular + TypeScript**, replacing the spec's Next.js (ADR 0004).
- **Workflow:** Claude writes all code and the user reviews everything. Work happens on one branch per feature (`feat/...`), pushed to GitHub where the user reviews before merge. Keep diffs reviewable, and explain the design and the reason for each piece in the PR description.
- **AWS: free-tier account.** Terraform must support spin-up/tear-down per session. Avoid always-on cost such as NAT gateways and idle Fargate tasks.
- **Domain:** local businesses. The first vertical template is a **bus travel agency** support desk (booking lookup, cancellations/refunds, schedule/route queries, baggage policy). The core stays domain-agnostic, and verticals are data/config (tools, policies, knowledge, eval sets), not forks of the code.
- Repo: https://github.com/muhammadsaad00-1/Self-Learn (`main` is protected by convention; never push directly after the initial commit).

## Build status
Roadmap phases are in spec 05. Mark items `[x]` as they land.
- [x] Phase 0 — Repo foundation: git, uv, ruff/mypy, pre-commit, Compose (Postgres+pgvector, Redis), config, CI, ADR template (branch `feat/phase-0-foundation`)
- [ ] Phase 1 — Core SaaS (split into PRs):
  - [x] 1a DB foundation: async SQLAlchemy, Alembic, identity tables, /ready, test DB fixtures (`feat/phase-1a-database`)
  - [ ] 1b Auth: signup/login/me, argon2, JWT
  - [ ] 1c Tenancy: org context, membership/role checks, RLS, cross-tenant tests, member invites
  - [ ] 1d Audit: append-only audit_events, same-transaction writes, GET /v1/audit/events
  - [ ] 1e Angular dashboard
- [ ] Phase 2 — First agent: model adapter, structured output, triage agent, LangGraph workflow, trace IDs, node tests
- [ ] Phase 3 — Knowledge: upload, async ingestion, chunking, embeddings, pgvector, hybrid retrieval, citations, RAG eval set
- [ ] Phase 4 — Multi-agent: supervisor, research, knowledge, resolution, verifier, budgets
- [ ] Phase 5 — Tools: registry, permission model, CRM/ticket/email/calendar/db tools, MCP, SQS/EventBridge automation
- [ ] Phase 6 — Approval: policy engine, approval entity + UI, pause/resume, expiry, double authorization
- [ ] Phase 7 — AWS: IaC, ECR, ECS/RDS/S3/SQS, Secrets/KMS, CloudWatch/OTel, staging/prod
- [ ] Phase 8 — Hardening: golden/adversarial/prompt-injection/cross-tenant/load/chaos/cost tests
- [ ] Phase 9–10 — Pilot, commercial MVP

## Open questions
See the latest conversation. Answers move into Decisions once settled.
