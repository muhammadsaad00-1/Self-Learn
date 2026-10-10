# 0006. Persistence: async SQLAlchemy 2, Alembic, request-scoped transactions

- Status: accepted
- Date: 2026-10-10

## Context
Phase 1 adds the first tables. We need a data-access layer that works with async FastAPI, a reviewable schema-migration history, and an easy way to guarantee that a domain change and its audit record commit together.

## Decision
- **SQLAlchemy 2 (async, typed `Mapped[...]`) + asyncpg.** ORM for domain code; raw `text()` SQL where Postgres features need it (RLS, pgvector).
- **Alembic** for migrations. Autogenerate is a *draft*: every migration is reviewed before commit. Config lives in `backend/pyproject.toml`, so CLI calls need `-c pyproject.toml` (the Makefile handles that).
- **One transaction per request** (`db.get_session`): commit on success, rollback on exception.
- **Enums are VARCHAR + an explicit CHECK** (`str_enum` + `enum_check`), not native Postgres ENUMs, which need `ALTER TYPE` to change.
- **Constraint naming convention** on the metadata, so every constraint has a predictable name that migrations can drop.
- **Tests use a real Postgres.** A `<db>_test` database is built by running the real migrations; each test runs inside a rolled-back transaction. Two guard tests: the models must match the migrated schema (drift check), and downgrade-to-base then upgrade must work.

## Consequences
- Domain writes and audit events (Phase 1d) are atomic for free.
- Long-running work (agent workflows) must not hold a request transaction open. Workers open their own short sessions (Phase 2+).
- Alembic 1.20 with SQLAlchemy 2.1 renders type-bound CHECKs (`Enum(create_constraint=True)`) twice. We avoid that by declaring CHECKs explicitly.

## Alternatives considered
- SQLModel: thinner, but lags SQLAlchemy features and blurs API schemas with tables. We want those kept separate.
- Raw asyncpg + SQL files: maximum control, but no unit of work, no drift detection, and more boilerplate.
- Native PG ENUM: migration friction.
