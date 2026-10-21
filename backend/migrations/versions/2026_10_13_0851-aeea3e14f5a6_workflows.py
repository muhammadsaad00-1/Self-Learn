"""workflows

Workflow records and their executed steps, both tenant-scoped with row-level security.

The worker must find queued work across *all* orgs, which RLS forbids for the app role. So
claiming goes through one narrow SECURITY DEFINER function: it runs with the owner's rights,
does exactly one thing (claim the next due workflow), and returns its id and org. The worker
then sets that org as tenant context and does everything else under RLS as usual.

Revision ID: aeea3e14f5a6
Revises: d319b7d16db6
Create Date: 2026-10-13 08:51:05.026254

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "aeea3e14f5a6"
down_revision: str | Sequence[str] | None = "d319b7d16db6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "completed",
                "failed",
                name="workflowstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name=op.f("ck_workflows_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_workflows_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflows")),
        sa.UniqueConstraint(
            "org_id", "idempotency_key", name=op.f("uq_workflows_org_id_idempotency_key")
        ),
    )
    op.create_index(
        "ix_workflows_org_id_created_at", "workflows", ["org_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_workflows_queue",
        "workflows",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("node", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("completed", "failed", name="stepstatus", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'failed')", name=op.f("ck_workflow_steps_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_workflow_steps_org_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name=op.f("fk_workflow_steps_workflow_id_workflows"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_steps")),
    )
    op.create_index(
        op.f("ix_workflow_steps_workflow_id"), "workflow_steps", ["workflow_id"], unique=False
    )

    # No DELETE: workflows are records of what happened. Steps are append-only.
    op.execute("GRANT SELECT, INSERT, UPDATE ON workflows TO agentflow_app")
    op.execute("GRANT SELECT, INSERT ON workflow_steps TO agentflow_app")
    for table in ("workflows", "workflow_steps"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (org_id = app_current_org_id())
            WITH CHECK (org_id = app_current_org_id())
        """)

    # FOR UPDATE SKIP LOCKED: concurrent workers each lock a *different* row instead of
    # waiting on (or double-claiming) the same one. Output columns are prefixed so they
    # can't shadow table columns inside PL/pgSQL.
    op.execute("""
        CREATE FUNCTION claim_next_workflow(max_attempts int, lease_seconds int)
        RETURNS TABLE (claimed_id uuid, claimed_org_id uuid, claimed_attempt int)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
            -- A lease that expired on the final attempt: the worker died every time.
            UPDATE workflows w
            SET status = 'failed', error = 'worker lease expired on the final attempt',
                locked_until = NULL, ended_at = now(), updated_at = now()
            WHERE w.status = 'running' AND w.locked_until < now()
              AND w.attempts >= max_attempts;

            RETURN QUERY
            UPDATE workflows w
            SET status = 'running', attempts = w.attempts + 1,
                locked_until = now() + make_interval(secs => lease_seconds),
                started_at = COALESCE(w.started_at, now()), updated_at = now()
            WHERE w.id = (
                SELECT c.id FROM workflows c
                WHERE c.available_at <= now() AND c.attempts < max_attempts
                  AND (c.status = 'queued' OR (c.status = 'running' AND c.locked_until < now()))
                ORDER BY c.available_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING w.id, w.org_id, w.attempts;
        END
        $$
    """)
    # Functions are executable by everyone by default; restrict it to the app role.
    op.execute("REVOKE ALL ON FUNCTION claim_next_workflow(int, int) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION claim_next_workflow(int, int) TO agentflow_app")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION claim_next_workflow(int, int)")
    op.drop_index(op.f("ix_workflow_steps_workflow_id"), table_name="workflow_steps")
    op.drop_table("workflow_steps")
    op.drop_index(
        "ix_workflows_queue",
        table_name="workflows",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_index("ix_workflows_org_id_created_at", table_name="workflows")
    op.drop_table("workflows")  # drops its policies and grants with it
