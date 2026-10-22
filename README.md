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

**Demo (≈5 min):**
1. Workflows → "Load demo customers" (also queues the customer help guide; wait until Knowledge shows it `ready`).
2. **Customer app** (separate from this dashboard): Chats shows the link `http://localhost:4200/customer/login?bank=<your org id>`. Sign in with a demo customer's mobile number, `0300 1234501` to `0300 1234505` (CUST-1001..1005). Outside production the one-time code is shown on screen. Tabs: Chat, Activity, Cases. Try *"I sent 3000 to Sara yesterday, she didn't get it"* (CUST-1004: the records show it was delivered), *"did my salary come?"* (CUST-1002), *"Daraz charged me twice"* (CUST-1001: only one charge), *"a bank officer called and I gave him my OTP"* (critical case + card frozen).
   Or, from the dashboard: Chats → "Open the customer app as…" → a customer → **Open customer app ↗** (a phone-style chat opens in a new tab). Try:
   - CUST-1004: *"Maine Ali ko 5000 bheje, kat gaye lekin usko nahi mile"* → the network says the transfer failed → reversed at once, transfer trace card, reply in Roman Urdu.
   - CUST-1002: *"How do I issue a cheque?"* (or 🎤 a voice note) → answered from the help guide with its source.
   - CUST-1003: *"I see payments I never made!"* → card frozen instantly + critical case TCK-… (Tickets page).
   - CUST-1005: *"I sent 15000 to Kamran yesterday, still not received"* → stuck in transit → partner-bank inquiry case.
   - CUST-1002: *"Careem charged me twice"* → refunded automatically. CUST-1001: *"My 5,000 bill was deducted twice"* → "with an officer"; a second admin approves in Approvals and the refund lands in the same chat.
3. Tickets: hand a case to a teammate with a note ("Hand off"), filter "mine", resolve with a message to the customer (it appears in their chat and their Cases tab).
4. Back in Chats: watch every conversation; type a reply to take over from the AI, then "Hand back to AI".
5. Insights: AI resolution rate, money returned, time to resolve, AI cost per request.
6. Workflows → any request → the decision trail (criticality signals, evidence → policy → approval → ledger-verified action → reply); Audit log for the compliance record.
`make help` lists every command. `make down` stops the stack.

## Test

```bash
make check       # everything CI runs: lint, types, secret scan, backend + frontend tests
make eval        # triage agent vs the real model
make eval-rag    # RAG retrieval quality per stage; AGENTS=1 adds the multi-agent pipeline
```
