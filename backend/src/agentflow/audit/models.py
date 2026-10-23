"""The audit_events table. Rows are never updated or deleted: the app role has no UPDATE or
DELETE grant, and a trigger rejects both even for the schema owner (see its migration)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_org_id_id", "org_id", "id"),)  # newest-first paging

    # Increasing integer, not a UUID: gives a stable order for paging (`before_id`), even for
    # several events written in one transaction (they share the same now() timestamp).
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # RESTRICT (the default): an organization with audit history can't be hard-deleted.
    # Financial records must be retained; orgs are suspended instead.
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    # No foreign key on purpose: the record must outlive the user it mentions.
    # None means the system acted (e.g. a scheduled job), not a person.
    actor_user_id: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(String(64))  # "<entity>.<verb>", e.g. "member.added"
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(64))
    # Ids and enum values only: no secrets, no free-text personal data (data minimization).
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    previous_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
