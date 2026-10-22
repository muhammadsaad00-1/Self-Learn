"""ticket reasons and customer message

Structured copies of what the description summarizes, so the UI can show "why a human
is needed" and the customer's own words without parsing text.

Revision ID: 1b889dccf150
Revises: 95f31d26384d
Create Date: 2026-10-23 04:22:29.745634

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1b889dccf150"
down_revision: str | Sequence[str] | None = "95f31d26384d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "tickets",
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("tickets", sa.Column("customer_message", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tickets", "customer_message")
    op.drop_column("tickets", "reasons")
