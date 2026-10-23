"""business profile, dispute evidence, customer email

The business profile from signup (organizations.profile); how each payment was made
(channel, device, OTP) for dispute intelligence; the customer's email for the email
channel. No grant changes: the app already updates organizations and inserts ledger rows.

Revision ID: 8901bf30be6d
Revises: 03542bc1e423
Create Date: 2026-10-23 04:57:37.376187

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8901bf30be6d"
down_revision: str | Sequence[str] | None = "03542bc1e423"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("ledger_accounts", sa.Column("email", sa.String(length=320), nullable=True))
    op.create_unique_constraint(
        op.f("uq_ledger_accounts_org_id_email"), "ledger_accounts", ["org_id", "email"]
    )
    op.add_column("ledger_transactions", sa.Column("channel", sa.String(length=32), nullable=True))
    op.add_column("ledger_transactions", sa.Column("device", sa.String(length=200), nullable=True))
    op.add_column("ledger_transactions", sa.Column("device_known", sa.Boolean(), nullable=True))
    op.add_column("ledger_transactions", sa.Column("verified", sa.Boolean(), nullable=True))
    op.add_column(
        "organizations",
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("organizations", "profile")
    op.drop_column("ledger_transactions", "verified")
    op.drop_column("ledger_transactions", "device_known")
    op.drop_column("ledger_transactions", "device")
    op.drop_column("ledger_transactions", "channel")
    op.drop_constraint(op.f("uq_ledger_accounts_org_id_email"), "ledger_accounts", type_="unique")
    op.drop_column("ledger_accounts", "email")
