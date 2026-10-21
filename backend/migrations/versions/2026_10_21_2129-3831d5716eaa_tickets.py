"""tickets

Cases a human must own, opened automatically by the workflow for critical outcomes.
Tenant data: RLS + least-privilege grants (no DELETE: tickets are records).

Revision ID: 3831d5716eaa
Revises: d8828be0588f
Create Date: 2026-10-21 21:29:47.757842

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3831d5716eaa"
down_revision: str | Sequence[str] | None = "d8828be0588f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.BigInteger(), sa.Identity(always=False, start=1000), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "priority",
            sa.Enum(
                "low",
                "normal",
                "high",
                "critical",
                name="ticketpriority",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("customer_ref", sa.String(length=100), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "open", "in_progress", "resolved", name="ticketstatus", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
            "priority IN ('low', 'normal', 'high', 'critical')",
            name=op.f("ck_tickets_priority_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved')", name=op.f("ck_tickets_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_tickets_org_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], name=op.f("fk_tickets_workflow_id_workflows")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint("number", name=op.f("uq_tickets_number")),
        sa.UniqueConstraint("workflow_id", name=op.f("uq_tickets_workflow_id")),
    )
    op.create_index(
        "ix_tickets_org_id_open",
        "tickets",
        ["org_id", "created_at"],
        unique=False,
        postgresql_where=sa.text("status <> 'resolved'"),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON tickets TO agentflow_app")
    op.execute("ALTER TABLE tickets ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON tickets
        USING (org_id = app_current_org_id())
        WITH CHECK (org_id = app_current_org_id())
    """)


def downgrade() -> None:
    op.drop_index(
        "ix_tickets_org_id_open",
        table_name="tickets",
        postgresql_where=sa.text("status <> 'resolved'"),
    )
    op.drop_table("tickets")
