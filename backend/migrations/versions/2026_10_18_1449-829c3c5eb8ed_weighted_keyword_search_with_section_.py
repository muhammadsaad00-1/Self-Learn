"""weighted keyword search with section heading

Keyword search ignored section headings: a chunk headed "Duplicate charges" whose body says
"debited more than once" didn't match the query "charged twice". The search column now indexes
heading (weight A) + body (weight B), so heading matches also rank higher.

A generated column's expression can't be altered in place on every Postgres version we may
run (SET EXPRESSION needs 17+), so the column and its index are dropped and re-created.
Postgres recomputes the values for existing rows.

Expressions are written out here, not imported from the model: a migration must stay a
frozen record of the change, even after the model moves on.

Revision ID: 829c3c5eb8ed
Revises: fff363daa4ab
Create Date: 2026-10-18 14:49:31.561465

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "829c3c5eb8ed"
down_revision: str | Sequence[str] | None = "fff363daa4ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD = "to_tsvector('english', content)"
NEW = (
    "setweight(to_tsvector('english', coalesce(section, '')), 'A') || "
    "setweight(to_tsvector('english', content), 'B')"
)


def _recreate_search_column(expression: str) -> None:
    op.drop_index("ix_knowledge_chunks_search", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "search")
    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "search",
            postgresql.TSVECTOR(),
            sa.Computed(expression, persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_search", "knowledge_chunks", ["search"], postgresql_using="gin"
    )


def upgrade() -> None:
    _recreate_search_column(NEW)


def downgrade() -> None:
    _recreate_search_column(OLD)
