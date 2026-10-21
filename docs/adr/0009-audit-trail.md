# 0009. Audit trail: append-only table, written in the caller's transaction

- Status: accepted
- Date: 2026-10-11

## Context
FR-012: every material workflow step produces an audit record. For a fintech pitch, the audit trail is what lets a regulator verify who approved a refund and why. It must be complete (no change without a record), truthful (no record without a change) and tamper-resistant.

## Decision
- One `audit_events` table: `org_id`, `actor_user_id` (null = system), `action` (`<entity>.<verb>`), `target_type`/`target_id`, `details` JSONB, `occurred_at`, and a BIGINT identity `id` used for ordering and keyset pagination.
- `audit.service.record()` only adds the row to the **caller's transaction** and never commits. The change and its event commit or roll back together.
- Immutability in three layers: the app role has only `SELECT, INSERT`; RLS limits each org to its own events; a `BEFORE UPDATE OR DELETE` trigger rejects changes even from the schema owner.
- `actor_user_id` has no foreign key, so the record outlives the user. `org_id` uses RESTRICT, so orgs with history are suspended, never hard-deleted.
- `details` holds ids and enum values only, never secrets or free-text personal data.
- Reading: `GET /v1/audit/events` for admins and owners, newest first, filter by `action`, `before_id` cursor.

## Consequences
- Login attempts (no org yet) aren't audit events. They belong in security logs (structured logging, Phase 2).
- Not cryptographically tamper-evident: a superuser with direct DB access could drop the trigger. Hash-chaining events per org (each row stores the hash of the previous one) is the upgrade path if a compliance requirement demands it.
- Growth: one table for all orgs. Monthly partitioning plus S3 archival (spec 02 §2) when volume warrants.

## Alternatives considered
- Writing audit events asynchronously (queue/outbox): decoupled, but events could be lost or recorded for rolled-back changes. Same-transaction is simpler and truthful.
- Postgres trigger-based auditing of every table: complete, but records low-level row diffs instead of business actions ("member.role_changed").
