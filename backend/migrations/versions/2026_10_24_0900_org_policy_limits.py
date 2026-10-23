"""Version tenant policy limits and enforce tenant RLS.

Revision ID: 5bc0c48f6a31
Revises: 2aa46e6917d0
Create Date: 2026-10-24 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5bc0c48f6a31"
down_revision: str | Sequence[str] | None = "2aa46e6917d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_policies",
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("auto_approve_limit", sa.Numeric(14, 2), nullable=False),
        sa.Column("max_refund", sa.Numeric(14, 2), nullable=False),
        sa.Column("max_reversals_per_day", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "auto_approve_limit >= 0", name=op.f("ck_org_policies_auto_approve_nonnegative")
        ),
        sa.CheckConstraint("max_refund > 0", name=op.f("ck_org_policies_max_refund_positive")),
        sa.CheckConstraint(
            "max_refund >= auto_approve_limit", name=op.f("ck_org_policies_refund_limit_order")
        ),
        sa.CheckConstraint(
            "max_reversals_per_day BETWEEN 1 AND 100",
            name=op.f("ck_org_policies_reversal_limit_range"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name=op.f("fk_org_policies_org_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("org_id", name=op.f("pk_org_policies")),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON org_policies TO agentflow_app")
    op.execute("ALTER TABLE org_policies ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON org_policies
        USING (org_id = app_current_org_id())
        WITH CHECK (org_id = app_current_org_id())
    """)


def downgrade() -> None:
    op.drop_table("org_policies")
