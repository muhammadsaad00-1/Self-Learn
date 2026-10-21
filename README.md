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

**Demo (≈3 min):**
1. Knowledge → upload `backend/evals/rag/corpus/refunds-and-disputes.md`.
2. Workflows → "Load demo customers".
3. Customer CUST-1002: *"Careem charged me twice"* → refunded automatically.
4. Customer CUST-1001: *"My PKR 5,000 bill was deducted twice"* → waits for approval.
5. Add a second admin (Members), sign in as them, Approvals → approve. The refund executes and the decision trail shows evidence → policy → approval → ledger-verified refund → drafted reply. `make help` lists every command. `make down` stops the stack.

## Test

```bash
make check       # everything CI runs: lint, types, secret scan, backend + frontend tests
make eval        # triage agent vs the real model
make eval-rag    # RAG retrieval quality per stage; AGENTS=1 adds the multi-agent pipeline
```
