"""Trips a transport company sells seats on (tenant data, RLS). Imported or entered by the
company; the agents answer "is there a seat to Lahore at 5pm?" from these rows."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base, TimestampMixin, enum_check, str_enum


class TripStatus(StrEnum):
    SCHEDULED = "scheduled"
    DELAYED = "delayed"
    CANCELLED = "cancelled"
    DEPARTED = "departed"


class Trip(TimestampMixin, Base):
    __tablename__ = "trips"
    __table_args__ = (
        UniqueConstraint("org_id", "trip_ref"),
        enum_check("status", TripStatus),
        Index("ix_trips_org_id_departs_at", "org_id", "departs_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    trip_ref: Mapped[str] = mapped_column(String(50))  # the company's id, e.g. "KHI-LHE-1700"
    origin: Mapped[str] = mapped_column(String(100))
    destination: Mapped[str] = mapped_column(String(100))
    departs_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    arrives_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fare: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    seats_total: Mapped[int | None]
    seats_available: Mapped[int]
    status: Mapped[TripStatus] = mapped_column(str_enum(TripStatus), default=TripStatus.SCHEDULED)
    vehicle: Mapped[str | None] = mapped_column(String(100))  # "Executive AC coach"
    notes: Mapped[str | None] = mapped_column(String(500))
