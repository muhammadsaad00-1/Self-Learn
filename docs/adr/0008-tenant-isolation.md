# 0008. Tenant isolation: org context per request + Postgres row-level security

- Status: accepted
- Date: 2026-10-11

## Context
Many organizations share one database (spec 02 §5). A single missing `WHERE org_id = ...` must not leak one tenant's data to another. In fintech that is a regulatory incident, not just a bug.

## Decision
Two independent layers:

1. **Application.** Tenant endpoints depend on `OrgContextDep`. The org comes from the `X-Org-Id` header, and membership + role are checked **against the database on every request**, not baked into the JWT. Every query also filters by `org_id` explicitly. Roles: owner > admin > member (`require_role`).
2. **Database (defense in depth).** Postgres row-level security on every table with an `org_id`. The app connects as `agentflow_app` (not superuser, not owner, `NOBYPASSRLS`) and sets `app.current_org_id` / `app.current_user_id` with transaction-local `set_config`. With no context set, tenant tables appear empty.

Supporting rules:
- Migrations run as the schema owner; the app role gets explicit per-table grants (no default privileges), so a new table is unreachable until its migration decides grants and policy.
- A test fails if any table with an `org_id` column lacks RLS.
- `users` has no RLS: accounts are global, and login must find a user before any tenant is known.
- One 403 message for "unknown org", "not a member" and "suspended", so org ids can't be probed.
- Membership changes lock the org row (`SELECT ... FOR UPDATE`) so concurrent demotions can't leave an org without an owner.

## Consequences
- RLS hides other tenants' rows from *every* app query, including uniqueness checks (org slugs now use insert-and-retry inside a SAVEPOINT).
- Background workers (Phase 2+) must call `set_tenant_context` before touching tenant data, or they see nothing. That is the safe failure mode.
- Local DBs created before this ADR need `make db-reset` (the app role is created on volume init).
- Phase 7 (RDS): Terraform creates the app role with a Secrets Manager password; the master user runs migrations.

## Alternatives considered
- Schema- or database-per-tenant: strongest isolation, but migrations × tenants and connection-pool explosion. Overkill for SMB tenants.
- Application filtering only: one forgotten filter means a leak.
- Org id inside the JWT: removals and role changes would only apply after token expiry.
