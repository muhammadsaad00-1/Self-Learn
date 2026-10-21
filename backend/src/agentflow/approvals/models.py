import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base, enum_check, str_enum


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"  # nobody decided in time: the action is not executed


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        enum_check("status", ApprovalStatus),
        Index(
            "ix_approvals_org_id_pending",
            "org_id",
            "requested_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id"), unique=True)
    action: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the proposed action, frozen
    policy_reasons: Mapped[list[str]] = mapped_column(JSONB)  # why approval is needed
    status: Mapped[ApprovalStatus] = mapped_column(
        str_enum(ApprovalStatus), default=ApprovalStatus.PENDING
    )
    requested_by: Mapped[uuid.UUID | None]  # the workflow's submitter (the "maker")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None]  # the "checker"
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)
