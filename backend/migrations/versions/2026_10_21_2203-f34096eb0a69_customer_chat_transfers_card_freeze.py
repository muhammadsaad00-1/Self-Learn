"""customer chat, transfers, card freeze

Conversations and chat messages (tenant data: RLS, no DELETE), the payment network's status
on transfers, and the card status the freeze tool sets (the app may update only that column).

Revision ID: f34096eb0a69
Revises: 3831d5716eaa
Create Date: 2026-10-21 22:03:39.807714

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f34096eb0a69"
down_revision: str | Sequence[str] | None = "3831d5716eaa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("customer_ref", sa.String(length=100), nullable=False),
        sa.Column(
            "mode",
            sa.Enum("ai", "human", name="conversationmode", native_enum=False, length=32),
            nullable=False,
        ),
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
        sa.CheckConstraint("mode IN ('ai', 'human')", name=op.f("ck_conversations_mode_valid")),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_conversations_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint(
            "org_id", "customer_ref", name=op.f("uq_conversations_org_id_customer_ref")
        ),
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "customer", "assistant", "agent", name="messagerole", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("card", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('customer', 'assistant', 'agent')", name=op.f("ck_chat_messages_role_valid")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_chat_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_chat_messages_org_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], name=op.f("fk_chat_messages_workflow_id_workflows")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
    )
    op.create_index(
        "ix_chat_messages_conversation_id_created_at",
        "chat_messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chat_messages_workflow_id"), "chat_messages", ["workflow_id"], unique=False
    )
    op.add_column(
        "ledger_accounts",
        sa.Column(
            "card_status",
            sa.Enum("active", "frozen", name="cardstatus", native_enum=False, length=32),
            server_default="active",
            nullable=False,
        ),
    )
    op.add_column(
        "ledger_transactions",
        sa.Column(
            "transfer_status",
            sa.Enum(
                "credited", "pending", "failed", name="transferstatus", native_enum=False, length=32
            ),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f("ck_ledger_accounts_card_status_valid"),
        "ledger_accounts",
        "card_status IN ('active', 'frozen')",
    )
    op.create_check_constraint(
        op.f("ck_ledger_transactions_transfer_status_valid"),
        "ledger_transactions",
        "transfer_status IN ('credited', 'pending', 'failed')",
    )
    op.execute("GRANT UPDATE (card_status) ON ledger_accounts TO agentflow_app")
    for table, privileges in {
        "conversations": "SELECT, INSERT, UPDATE",
        "chat_messages": "SELECT, INSERT",  # a chat transcript is a record: append-only
    }.items():
        op.execute(f"GRANT {privileges} ON {table} TO agentflow_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (org_id = app_current_org_id())
            WITH CHECK (org_id = app_current_org_id())
        """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("REVOKE UPDATE (card_status) ON ledger_accounts FROM agentflow_app")
    op.drop_column("ledger_transactions", "transfer_status")
    op.drop_column("ledger_accounts", "card_status")
    op.drop_index(op.f("ix_chat_messages_workflow_id"), table_name="chat_messages")
    op.drop_index("ix_chat_messages_conversation_id_created_at", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("conversations")
