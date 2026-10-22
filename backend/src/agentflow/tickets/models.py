import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base, TimestampMixin, enum_check, str_enum


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"  # someone took it
    RESOLVED = "resolved"


class TicketPriority(StrEnum):  # mirrors triage urgency
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        enum_check("status", TicketStatus),
        enum_check("priority", TicketPriority),
        Index(
            "ix_tickets_org_id_open",
            "org_id",
            "created_at",
            postgresql_where=text("status <> 'resolved'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Human-friendly case number ("TCK-1042"), increasing; unique across all orgs.
    number: Mapped[int] = mapped_column(BigInteger, Identity(start=1000), unique=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    # One ticket per workflow at most (also enforced by the action ledger).
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflows.id"), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[TicketPriority] = mapped_column(str_enum(TicketPriority))
    category: Mapped[str | None] = mapped_column(String(64))
    customer_ref: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[TicketStatus] = mapped_column(str_enum(TicketStatus), default=TicketStatus.OPEN)
    assignee_id: Mapped[uuid.UUID | None]
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Hand-offs and notes, oldest first: [{"at", "by", "kind", "text", "to"?}]. The audit
    # log has the same events; this copy is what the team reads on the ticket.
    activity: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )

    @property
    def code(self) -> str:
        return f"TCK-{self.number}"
