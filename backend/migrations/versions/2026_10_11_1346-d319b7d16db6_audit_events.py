"""audit events

Append-only audit trail. Three protections, from the app role up to the schema owner:
  1. grants: the app role may only SELECT and INSERT (no UPDATE/DELETE privilege at all)
  2. row-level security: an org reads and writes only its own events
  3. a trigger rejects UPDATE and DELETE for everyone, including the owner

Revision ID: d319b7d16db6
Revises: 562924d824f0
Create Date: 2026-10-11 13:46:20.089181

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d319b7d16db6"
down_revision: str | Sequence[str] | None = "562924d824f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_audit_events_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index("ix_audit_events_org_id_id", "audit_events", ["org_id", "id"], unique=False)

    op.execute("GRANT SELECT, INSERT ON audit_events TO agentflow_app")

    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON audit_events
        USING (org_id = app_current_org_id())
        WITH CHECK (org_id = app_current_org_id())
    """)

    op.execute("""
        CREATE FUNCTION reject_audit_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% rejected)', TG_OP;
        END
        $$
    """)
    # TRUNCATE stays possible for the owner only (test cleanup, future retention jobs);
    # the app role was never granted it.
    op.execute("""
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_audit_change()
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION reject_audit_change()")
    op.drop_index("ix_audit_events_org_id_id", table_name="audit_events")
    op.drop_table("audit_events")  # drops its policy and grants with it
