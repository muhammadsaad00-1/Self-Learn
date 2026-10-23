"""Return expired approvals so the worker can hash-chain their audit events.

Revision ID: 2d4aa2719b60
Revises: a1f90dcb34e8
Create Date: 2026-10-24 09:30:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "2d4aa2719b60"
down_revision: str | Sequence[str] | None = "a1f90dcb34e8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("DROP FUNCTION expire_approvals()")
    op.execute("""
        CREATE FUNCTION expire_approvals()
        RETURNS TABLE(org_id uuid, approval_id uuid, workflow_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
            RETURN QUERY
            WITH e AS (
                UPDATE public.approvals a
                SET status = 'expired', decided_at = now()
                WHERE a.status = 'pending' AND a.expires_at < now()
                RETURNING a.org_id, a.id, a.workflow_id
            ), requeued AS (
                UPDATE public.workflows w
                SET status = 'queued', stage = 'execution', available_at = now(),
                    locked_until = NULL, updated_at = now()
                FROM e
                WHERE w.id = e.workflow_id AND w.status = 'waiting_approval'
                RETURNING w.id
            )
            SELECT e.org_id, e.id, e.workflow_id FROM e;
        END
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION expire_approvals() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION expire_approvals() TO agentflow_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION expire_approvals()")
    op.execute("""
        CREATE FUNCTION expire_approvals() RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE expired integer;
        BEGIN
            WITH e AS (
                UPDATE approvals SET status = 'expired', decided_at = now()
                WHERE status = 'pending' AND expires_at < now()
                RETURNING workflow_id
            )
            UPDATE workflows w
            SET status = 'queued', stage = 'execution', available_at = now(),
                locked_until = NULL, updated_at = now()
            FROM e WHERE w.id = e.workflow_id AND w.status = 'waiting_approval';
            GET DIAGNOSTICS expired = ROW_COUNT;
            RETURN expired;
        END
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION expire_approvals() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION expire_approvals() TO agentflow_app")
