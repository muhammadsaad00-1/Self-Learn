"""Workflow state lives in Postgres, not in the model or in worker memory (spec 01 §8):
any worker can pick a workflow up, and a crash loses nothing that was committed."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base, TimestampMixin, enum_check, str_enum


class WorkflowStatus(StrEnum):
    QUEUED = "queued"  # waiting for a worker (new, or retrying after a failure)
    RUNNING = "running"  # a worker holds the lease
    COMPLETED = "completed"
    FAILED = "failed"  # gave up after max attempts (dead-lettered); needs a human


class StepStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class Workflow(TimestampMixin, Base):
    __tablename__ = "workflows"
    __table_args__ = (
        enum_check("status", WorkflowStatus),
        # The same Idempotency-Key within an org always means the same workflow.
        UniqueConstraint("org_id", "idempotency_key"),
        # The worker's "next job" query reads only this small partial index.
        Index(
            "ix_workflows_queue",
            "available_at",
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_workflows_org_id_created_at", "org_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    created_by: Mapped[uuid.UUID | None]  # user id; no FK, like audit actors
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_fingerprint: Mapped[str] = mapped_column(String(64))  # sha256 of the request body
    request_id: Mapped[str | None] = mapped_column(String(64))  # correlates logs across services
    status: Mapped[WorkflowStatus] = mapped_column(
        str_enum(WorkflowStatus), default=WorkflowStatus.QUEUED
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    # Not before this time: a failed attempt is retried later (exponential backoff).
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # While running: the worker's lease. Past it, the worker is presumed dead.
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowStep(Base):
    """One executed graph node: what it produced and, for AI nodes, what it cost."""

    __tablename__ = "workflow_steps"
    __table_args__ = (enum_check("status", StepStatus),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    node: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int]
    status: Mapped[StepStatus] = mapped_column(str_enum(StepStatus))
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    # Set for nodes that called a model (FR-016: model, tokens, latency per call).
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    latency_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
