"""chunk quarantine

Chunks flagged as prompt injection at ingestion are stored but never retrieved (see
knowledge/screening.py). server_default: existing rows need a value for NOT NULL columns.

Revision ID: 9c0ad2ec4f64
Revises: a3a92172870a
Create Date: 2026-10-20 08:55:04.348081

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c0ad2ec4f64"
down_revision: str | Sequence[str] | None = "a3a92172870a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_chunks",
        sa.Column("quarantined", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "knowledge_chunks", sa.Column("quarantine_reason", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("quarantined_chunks", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("knowledge_documents", "quarantined_chunks")
    op.drop_column("knowledge_chunks", "quarantine_reason")
    op.drop_column("knowledge_chunks", "quarantined")
