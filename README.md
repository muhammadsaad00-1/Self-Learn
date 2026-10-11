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

Sign up at http://localhost:4200/signup. `make help` lists every command. `make down` stops the stack.

## Test

```bash
make check       # everything CI runs: lint, types, secret scan, backend + frontend tests
```
