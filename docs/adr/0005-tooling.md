# 0005. Python tooling: uv, ruff, mypy (strict), pytest, pre-commit

- Status: accepted
- Date: 2026-10-06

## Context
Phase 0 requires project management, lint/type tooling, pre-commit hooks and CI (05 §2).

## Decision
- **uv** manages the Python version (3.12), dependencies and the lockfile (`uv.lock`, committed).
- **ruff** does linting and formatting, replacing flake8, isort and black.
- **mypy --strict** type-checks `src/`, so typed contracts are enforced, not just written down.
- **pytest** for tests. Integration tests run against real Postgres and Redis from Docker Compose.
- **pre-commit** runs the same checks locally that CI runs, plus secret scanning (gitleaks) and a conventional-commit message check.

## Consequences
- One command (`make check`) gives the same verdict locally and in CI.
- Strict mypy slows early coding slightly, and in return catches contract bugs before runtime.

## Alternatives considered
- Poetry: slower, and does not manage Python versions.
- black + isort + flake8: three tools where one does the job.
