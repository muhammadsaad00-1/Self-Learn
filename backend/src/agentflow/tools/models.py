"""Action ledger: one row per side effect, keyed for exactly-once execution (spec 02 §7)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base


class ActionExecution(Base):
    __tablename__ = "action_executions"
    # The idempotency key identifies the *logical* action: the same refund of the same
    # transaction for the same workflow can only ever execute once, however often retried.
    __table_args__ = (UniqueConstraint("org_id", "idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id"))
    tool: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64))  # sha256 hex
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
