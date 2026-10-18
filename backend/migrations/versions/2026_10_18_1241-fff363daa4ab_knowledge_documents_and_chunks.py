"""knowledge documents and chunks

Documents (one per uploaded file) and chunks (searchable pieces with a vector embedding and a
keyword index). Both are tenant data: row-level security like every org-owned table.

Revision ID: fff363daa4ab
Revises: aeea3e14f5a6
Create Date: 2026-10-18 12:41:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "fff363daa4ab"
down_revision: str | Sequence[str] | None = "aeea3e14f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector adds the `vector` column type, distance operators (<=> is cosine distance) and
    # the HNSW index method. Creating an extension needs elevated rights: fine for the schema
    # owner locally and for the RDS master user; never needed by the app role.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("filename", sa.String(length=300), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name=op.f("ck_knowledge_documents_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_knowledge_documents_org_id_organizations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_documents")),
        sa.UniqueConstraint(
            "org_id", "checksum", name=op.f("uq_knowledge_documents_org_id_checksum")
        ),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=300), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=False),
        sa.Column(
            "search",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', content)", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            name=op.f("fk_knowledge_chunks_document_id_knowledge_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_knowledge_chunks_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
        sa.UniqueConstraint(
            "document_id", "ordinal", name=op.f("uq_knowledge_chunks_document_id_ordinal")
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_embedding",
        "knowledge_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_knowledge_chunks_search", "knowledge_chunks", ["search"], postgresql_using="gin"
    )

    # Documents can be deleted (chunks go with them via CASCADE). Re-ingesting a document
    # deletes and re-inserts its chunks, so chunks need DELETE but never UPDATE.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge_documents TO agentflow_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON knowledge_chunks TO agentflow_app")
    for table in ("knowledge_documents", "knowledge_chunks"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (org_id = app_current_org_id())
            WITH CHECK (org_id = app_current_org_id())
        """)


def downgrade() -> None:
    # Tables take their indexes, policies and grants with them. The extension stays: other
    # objects may use it, and dropping it is a database-wide decision.
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
