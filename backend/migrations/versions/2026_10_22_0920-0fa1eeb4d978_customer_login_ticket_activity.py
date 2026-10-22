"""customer login, ticket activity

Customers log in with their registered mobile number (unique per bank). Tickets keep a
hand-off/notes trail. No grant changes: tickets already allow UPDATE, phone is never
updated by the app.

Revision ID: 0fa1eeb4d978
Revises: f34096eb0a69
Create Date: 2026-10-22 09:20:24.740156

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0fa1eeb4d978"
down_revision: str | Sequence[str] | None = "f34096eb0a69"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("ledger_accounts", sa.Column("phone", sa.String(length=20), nullable=True))
    op.create_unique_constraint(
        op.f("uq_ledger_accounts_org_id_phone"), "ledger_accounts", ["org_id", "phone"]
    )
    op.add_column(
        "tickets",
        sa.Column(
            "activity",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tickets", "activity")
    op.drop_constraint(op.f("uq_ledger_accounts_org_id_phone"), "ledger_accounts", type_="unique")
    op.drop_column("ledger_accounts", "phone")
