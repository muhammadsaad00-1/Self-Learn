"""ledger tools approvals

Phase 4-6: a simulated core-banking ledger for the agents' tools, an action ledger for
exactly-once side effects, human approvals (maker-checker), and workflow pause/resume
(status waiting_approval + stage). All tenant tables get RLS and least-privilege grants.
expire_approvals() is the worker's narrow cross-tenant capability to time approvals out.

Revision ID: d8828be0588f
Revises: 9c0ad2ec4f64
Create Date: 2026-10-21 21:12:36.416711

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d8828be0588f"
down_revision: str | Sequence[str] | None = "9c0ad2ec4f64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ledger_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("customer_ref", sa.String(length=100), nullable=False),
        sa.Column("customer_name", sa.String(length=200), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_ledger_accounts_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_accounts")),
        sa.UniqueConstraint(
            "org_id", "customer_ref", name=op.f("uq_ledger_accounts_org_id_customer_ref")
        ),
    )
    op.create_table(
        "action_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_action_executions_org_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name=op.f("fk_action_executions_workflow_id_workflows"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_executions")),
        sa.UniqueConstraint(
            "org_id", "idempotency_key", name=op.f("uq_action_executions_org_id_idempotency_key")
        ),
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("action", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "expired",
                name="approvalstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')",
            name=op.f("ck_approvals_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_approvals_org_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], name=op.f("fk_approvals_workflow_id_workflows")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
        sa.UniqueConstraint("workflow_id", name=op.f("uq_approvals_workflow_id")),
    )
    op.create_index(
        "ix_approvals_org_id_pending",
        "approvals",
        ["org_id", "requested_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "ledger_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.String(length=50), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("debit", "credit", name="transactionkind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("merchant", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum("posted", "reversed", name="transactionstatus", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("reverses_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('debit', 'credit')", name=op.f("ck_ledger_transactions_kind_valid")
        ),
        sa.CheckConstraint(
            "status IN ('posted', 'reversed')", name=op.f("ck_ledger_transactions_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["ledger_accounts.id"],
            name=op.f("fk_ledger_transactions_account_id_ledger_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_ledger_transactions_org_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["reverses_id"],
            ["ledger_transactions.id"],
            name=op.f("fk_ledger_transactions_reverses_id_ledger_transactions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_transactions")),
        sa.UniqueConstraint(
            "org_id", "reference", name=op.f("uq_ledger_transactions_org_id_reference")
        ),
    )
    op.create_index(
        "ix_ledger_transactions_account_id_occurred_at",
        "ledger_transactions",
        ["account_id", "occurred_at"],
        unique=False,
    )
    op.add_column(
        "workflows",
        sa.Column(
            "stage",
            sa.Enum("analysis", "execution", name="workflowstage", native_enum=False, length=32),
            server_default="analysis",  # existing workflows need a value
            nullable=False,
        ),
    )
    # Autogenerate doesn't compare CHECK constraints: widen status, constrain stage by hand.
    # op.f(): the name is final; otherwise the naming convention would prefix it again.
    op.drop_constraint(op.f("ck_workflows_status_valid"), "workflows", type_="check")
    op.create_check_constraint(
        op.f("ck_workflows_status_valid"),
        "workflows",
        "status IN ('queued', 'running', 'waiting_approval', 'completed', 'failed')",
    )
    op.create_check_constraint(
        op.f("ck_workflows_stage_valid"), "workflows", "stage IN ('analysis', 'execution')"
    )

    grants = {
        "ledger_accounts": "SELECT, INSERT",
        # Refunds flip a debit to 'reversed' and add a credit; rows are never deleted.
        "ledger_transactions": "SELECT, INSERT, UPDATE",
        "action_executions": "SELECT, INSERT",  # append-only record of side effects
        "approvals": "SELECT, INSERT, UPDATE",
    }
    for table, privileges in grants.items():
        op.execute(f"GRANT {privileges} ON {table} TO agentflow_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (org_id = app_current_org_id())
            WITH CHECK (org_id = app_current_org_id())
        """)

    # Pending approvals past their deadline expire, and their workflows are re-queued for
    # the execution stage, which reports "not executed (approval expired)".
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


def downgrade() -> None:
    op.execute("DROP FUNCTION expire_approvals()")
    op.drop_constraint(op.f("ck_workflows_stage_valid"), "workflows", type_="check")
    op.drop_constraint(op.f("ck_workflows_status_valid"), "workflows", type_="check")
    op.create_check_constraint(
        op.f("ck_workflows_status_valid"),
        "workflows",
        "status IN ('queued', 'running', 'completed', 'failed')",
    )
    op.drop_column("workflows", "stage")
    op.drop_index("ix_ledger_transactions_account_id_occurred_at", table_name="ledger_transactions")
    op.drop_table("ledger_transactions")
    op.drop_index(
        "ix_approvals_org_id_pending",
        table_name="approvals",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("approvals")
    op.drop_table("action_executions")
    op.drop_table("ledger_accounts")
