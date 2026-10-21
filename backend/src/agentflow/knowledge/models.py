"""Documents and their searchable chunks.

A document is one uploaded file (the original lives in object storage, not here). Ingestion
splits its text into chunks; each chunk is stored twice over for search:
  - `embedding`: a vector, for semantic search ("charged twice" ~ "duplicate debit")
  - `search`: a tsvector, for keyword search (exact terms such as "TXN-88231" or "IBFT")
Retrieval (step 6) combines both. Citations point at a chunk: document + section + position.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base, TimestampMixin, enum_check, str_enum

# Fixed by the embedding model (bge-small-en-v1.5 -> 384). A different model means a new
# column size and re-embedding every chunk, so the model name is stored on each row too.
EMBEDDING_DIMENSIONS = 384

# Keyword-search document for a chunk: heading words weighted A, body words weighted B.
SEARCH_EXPRESSION = (
    "setweight(to_tsvector('english', coalesce(section, '')), 'A') || "
    "setweight(to_tsvector('english', content), 'B')"
)


class DocumentStatus(StrEnum):
    PENDING = "pending"  # uploaded, waiting for the ingestion job
    PROCESSING = "processing"
    READY = "ready"  # chunks are searchable
    FAILED = "failed"


class Document(TimestampMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        enum_check("status", DocumentStatus),
        # Re-uploading the exact same file is a no-op instead of a second copy (idempotency).
        UniqueConstraint("org_id", "checksum"),
        # The ingestion worker's "next job" query reads only this small partial index.
        Index(
            "ix_knowledge_documents_queue",
            "available_at",
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    title: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    checksum: Mapped[str] = mapped_column(String(64))  # sha256 of the file bytes
    storage_key: Mapped[str] = mapped_column(String(500))  # where the original file lives
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus), default=DocumentStatus.PENDING
    )
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(default=0)
    # Chunks the injection screen quarantined (stored, never retrieved). See screening.py.
    quarantined_chunks: Mapped[int] = mapped_column(default=0)
    uploaded_by: Mapped[uuid.UUID | None]  # user id; no FK, like audit actors
    # Ingestion runs as a background job with the same mechanics as workflows (ADR 0010):
    # attempts + backoff via available_at, and a lease (locked_until) a crashed worker loses.
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Chunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal"),
        # Approximate nearest-neighbour index for cosine distance (the <=> operator).
        # HNSW: a layered graph of vectors; queries hop towards the closest ones instead of
        # comparing against every row. m / ef_construction trade build time and memory for
        # recall; these are pgvector's defaults, written out so they're visible.
        Index(
            "ix_knowledge_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # Inverted index for full-text (keyword) search on the generated tsvector.
        Index("ix_knowledge_chunks_search", "search", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int]  # position in the document: 0, 1, 2 ...
    section: Mapped[str | None] = mapped_column(String(300))  # nearest heading, for citations
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str] = mapped_column(String(100))
    # Flagged by the injection screen at ingestion: kept for review, excluded from retrieval.
    quarantined: Mapped[bool] = mapped_column(default=False)
    quarantine_reason: Mapped[str | None] = mapped_column(String(100))
    # Computed by Postgres on every insert/update, never written by us. The section heading is
    # indexed too, with weight A (body text: B), so ts_rank scores a match in the heading
    # higher: "Duplicate charges" is a stronger signal than "charges" in passing.
    search: Mapped[str] = mapped_column(TSVECTOR, Computed(SEARCH_EXPRESSION, persisted=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
