# AgentFlow

Governed AI agents for financial operations: agents investigate and propose, deterministic policy decides, humans approve risky actions, and everything is audited. Multi-tenant, fintech first, other services after.

- Hackathon proposal: [`docs/PROPOSAL.md`](docs/PROPOSAL.md)
- Specs: [`docs/spec/`](docs/spec/) (originals in `AgentFlow_Technical_Documentation/`)
- Decisions: [`docs/adr/`](docs/adr/)
- Project context and build status: [`CLAUDE.md`](CLAUDE.md)

## Run it locally

Needs Docker, [uv](https://docs.astral.sh/uv/) and Node 24 (`nvm use 24`).

```bash
make install     # Python + npm dependencies, git hooks
make up          # Postgres, Redis and the API in Docker  →  http://localhost:8000/docs
make migrate     # create the tables
make web         # Angular dashboard                      →  http://localhost:4200
```

`GROQ_API_KEY` goes in `.env` (never `.env.example`). The first document upload downloads the embedding and reranker models (~700 MB, cached in a Docker volume). Sample policy documents to upload: `backend/evals/rag/corpus/`.

Sign up at http://localhost:4200/signup.

**Demo (≈5 min):** the script, the pitch and the competitor comparison are in [`docs/PITCH.md`](docs/PITCH.md) §7. In short:
1. Sign up at /signup (3-step wizard) → Overview → **Load demo customers**.
2. Customers sign in at /customer/login with phone + username (demo: `0300 1234504` + `usman.tariq`; others: ayesha.khan, bilal.ahmed, hina.raza, zainab.ali, faraz.hussain for 01–06), or staff use Inbox → **Open**. Try a failed transfer (CUST-1004), *"my 3000 to Sara never arrived"* (CUST-1004), genuine fraud (CUST-1003) and a **false fraud claim** (CUST-1006: no freeze, the facts shown, a case for review).
3. Settings → **Load a sample inbox**: 8 emails through the same agents, including an injection attempt.
4. Tickets (hand off, resolve with a message), Approvals (maker-checker), Requests (the decision trail), Overview (ROI).
5. **Data** (admins): import customers, transactions (banks) or trips (transport) by hand, CSV, or a PDF report; review the rows in the grid, fix what's flagged, commit. Try "Try a sample" on each card. For a transport company, sign up with industry *Transport*, import the sample trips, then ask in the customer app: *"Is there a seat to Lahore tomorrow evening?"*

`make help` lists every command. `make down` stops the stack.

## How a customer message flows

```
customer app ─ Help chat ───────────► workflow: triage → plan → retrieve+rerank → answer → verify
                  │                                 │
                  │   right after triage, CODE decides: a problem (payment category or
                  │   critical risk)? → open an ISSUE thread, say so in the help chat
                  ▼                                 ▼
   answer from the company's docs     research (ledger) → resolve → policy → [approval]
   or the customer's own records      → execute (refund / reversal / card freeze / records answer)
   (stays in the help chat)           → ticket if a human must own it → reply in the issue
                                         issue status: resolved · in review · with a specialist
team dashboard: Chats (take over / hand back) · Tickets (hand off, resolve → message in the issue)
                Approvals (maker-checker) · Insights · Audit log
```

## See it work as a company (real models)

```bash
make up && make migrate      # the stack, with the worker
make journeys                # a new company uploads its policy document, 11 customers write in,
                             # the team approves, hands off and resolves; prints a pass/fail report
make journeys ONLY=fraud,otp-scam   # just some journeys
```
Expectations live in `backend/evals/journeys/journeys.jsonl` (route, issue status, cards, priority, a key fact in the reply). The free Groq tier allows ~200k tokens/day on gpt-oss-120b, roughly 15 full runs across all evals.

## Test

```bash
make check       # everything CI runs: lint, types, secret scan, backend + frontend tests
make eval        # triage agent vs the real model
make eval-rag    # RAG retrieval quality per stage; AGENTS=1 adds the multi-agent pipeline
```
