"""help chat and issue threads

A customer has one help chat and any number of issue threads (status, origin workflow,
ticket). Existing conversations become help chats.

Revision ID: 95f31d26384d
Revises: 0fa1eeb4d978
Create Date: 2026-10-22 23:19:27.154015

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "95f31d26384d"
down_revision: str | Sequence[str] | None = "0fa1eeb4d978"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "conversations",
        sa.Column(
            "kind",
            sa.Enum("help", "issue", name="conversationkind", native_enum=False, length=32),
            server_default="help",
            nullable=False,
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "in_review",
                "with_specialist",
                "resolved",
                name="issuestatus",
                native_enum=False,
                length=32,
            ),
            server_default="open",
            nullable=False,
        ),
    )
    op.add_column("conversations", sa.Column("subject", sa.String(length=300), nullable=True))
    op.add_column("conversations", sa.Column("origin_workflow_id", sa.Uuid(), nullable=True))
    op.add_column("conversations", sa.Column("ticket_id", sa.Uuid(), nullable=True))
    op.drop_constraint(
        op.f("uq_conversations_org_id_customer_ref"), "conversations", type_="unique"
    )
    op.create_index(
        "uq_conversations_help_per_customer",
        "conversations",
        ["org_id", "customer_ref"],
        unique=True,
        postgresql_where=sa.text("kind = 'help'"),
    )
    op.create_unique_constraint(
        op.f("uq_conversations_origin_workflow_id"), "conversations", ["origin_workflow_id"]
    )
    op.create_foreign_key(
        op.f("fk_conversations_ticket_id_tickets"),
        "conversations",
        "tickets",
        ["ticket_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_conversations_origin_workflow_id_workflows"),
        "conversations",
        "workflows",
        ["origin_workflow_id"],
        ["id"],
    )
    op.create_check_constraint(
        op.f("ck_conversations_kind_valid"), "conversations", "kind IN ('help', 'issue')"
    )
    op.create_check_constraint(
        op.f("ck_conversations_status_valid"),
        "conversations",
        "status IN ('open', 'in_review', 'with_specialist', 'resolved')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Back to one conversation per customer: issue threads (and, by cascade, their
    # messages) can't be represented.
    op.execute("DELETE FROM conversations WHERE kind = 'issue'")
    op.drop_constraint(
        op.f("fk_conversations_origin_workflow_id_workflows"), "conversations", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_conversations_ticket_id_tickets"), "conversations", type_="foreignkey"
    )
    op.drop_constraint(op.f("uq_conversations_origin_workflow_id"), "conversations", type_="unique")
    op.drop_index(
        "uq_conversations_help_per_customer",
        table_name="conversations",
        postgresql_where=sa.text("kind = 'help'"),
    )
    op.create_unique_constraint(
        op.f("uq_conversations_org_id_customer_ref"),
        "conversations",
        ["org_id", "customer_ref"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_column("conversations", "ticket_id")
    op.drop_column("conversations", "origin_workflow_id")
    op.drop_column("conversations", "subject")
    op.drop_column("conversations", "status")
    op.drop_column("conversations", "kind")
