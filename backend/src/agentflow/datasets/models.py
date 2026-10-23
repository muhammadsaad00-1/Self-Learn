"""Import batches: data a company brings in (typed, CSV, PDF), staged for human review before
anything reaches the real tables (tenant data, RLS)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base, TimestampMixin, enum_check, str_enum


class BatchStatus(StrEnum):
    EXTRACTING = "extracting"  # a PDF/text report: the extraction agent is reading it
    REVIEW = "review"  # rows staged: people check, edit, delete, add
    COMMITTED = "committed"  # written to the real tables (once)
    DISCARDED = "discarded"
    FAILED = "failed"  # extraction gave up


class BatchSource(StrEnum):
    MANUAL = "manual"
    CSV = "csv"
    DOCUMENT = "document"  # PDF / text report, read by the extraction agent
    SAMPLE = "sample"


class ImportBatch(TimestampMixin, Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        enum_check("status", BatchStatus),
        enum_check("source", BatchSource),
        Index(
            "ix_import_batches_queue",
            "available_at",
            postgresql_where=text("status = 'extracting'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    record_type: Mapped[str] = mapped_column(String(32))  # datasets.templates.RECORD_TYPES
    source: Mapped[BatchSource] = mapped_column(str_enum(BatchSource))
    filename: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[BatchStatus] = mapped_column(str_enum(BatchStatus))
    source_text: Mapped[str | None] = mapped_column(Text)  # what the extraction agent reads
    notes: Mapped[list[str]] = mapped_column(JSONB, default=list)  # e.g. ignored columns
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None]
    committed_by: Mapped[uuid.UUID | None]
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # created/updated/skipped
    # Background extraction (same mechanics as workflows and document ingestion).
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (Index("ix_import_rows_batch_id_position", "batch_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"))
    position: Mapped[int]
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)  # checked values (or the raw bad one)
    errors: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)  # field -> problem
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
