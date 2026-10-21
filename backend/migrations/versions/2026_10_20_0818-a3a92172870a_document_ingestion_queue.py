"""document ingestion queue

Ingestion (parse, chunk, embed) runs in the worker like workflows do: attempts with backoff,
a lease that a crashed worker loses, dead-lettering after max attempts. The claim function
mirrors claim_next_workflow: the worker's one narrow cross-tenant capability.

Revision ID: a3a92172870a
Revises: 829c3c5eb8ed
Create Date: 2026-10-20 08:18:52.294638

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3a92172870a"
down_revision: str | Sequence[str] | None = "829c3c5eb8ed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default: existing rows need a value, or adding a NOT NULL column fails.
    op.add_column(
        "knowledge_documents",
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "knowledge_documents", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_knowledge_documents_queue",
        "knowledge_documents",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.execute("""
        CREATE FUNCTION claim_next_document(max_attempts int, lease_seconds int)
        RETURNS TABLE (claimed_id uuid, claimed_org_id uuid, claimed_attempt int)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
            UPDATE knowledge_documents d
            SET status = 'failed', error = 'worker lease expired on the final attempt',
                locked_until = NULL, updated_at = now()
            WHERE d.status = 'processing' AND d.locked_until < now()
              AND d.attempts >= max_attempts;

            RETURN QUERY
            UPDATE knowledge_documents d
            SET status = 'processing', attempts = d.attempts + 1,
                locked_until = now() + make_interval(secs => lease_seconds), updated_at = now()
            WHERE d.id = (
                SELECT c.id FROM knowledge_documents c
                WHERE c.available_at <= now() AND c.attempts < max_attempts
                  AND (c.status = 'pending'
                       OR (c.status = 'processing' AND c.locked_until < now()))
                ORDER BY c.available_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING d.id, d.org_id, d.attempts;
        END
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION claim_next_document(int, int) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION claim_next_document(int, int) TO agentflow_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION claim_next_document(int, int)")
    op.drop_index(
        "ix_knowledge_documents_queue",
        table_name="knowledge_documents",
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )
    op.drop_column("knowledge_documents", "locked_until")
    op.drop_column("knowledge_documents", "available_at")
    op.drop_column("knowledge_documents", "attempts")
