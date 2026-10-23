"""Versioned, tenant-owned policy limits used by the deterministic action gate."""

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base


class OrgPolicy(Base):
    __tablename__ = "org_policies"
    __table_args__ = (
        CheckConstraint("auto_approve_limit >= 0", name="auto_approve_nonnegative"),
        CheckConstraint("max_refund > 0", name="max_refund_positive"),
        CheckConstraint("max_refund >= auto_approve_limit", name="refund_limit_order"),
        CheckConstraint("max_reversals_per_day BETWEEN 1 AND 100", name="reversal_limit_range"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    auto_approve_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    max_refund: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    max_reversals_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
