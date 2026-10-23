"""Trip reads and writes other modules use (the import pipeline, the agents' research)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.transport.models import Trip, TripStatus
from agentflow.transport.schemas import TripOut

UPCOMING_DAYS = 14
UPCOMING_LIMIT = 60  # what the agents see: enough for a schedule, small enough for a prompt


async def upsert_trip(session: AsyncSession, org_id: uuid.UUID, data: dict[str, Any]) -> bool:
    """Insert, or update the trip with the same `trip_ref`. True if it was new."""
    trip = await session.scalar(
        select(Trip).where(Trip.org_id == org_id, Trip.trip_ref == data["trip_ref"])
    )
    created = trip is None
    if trip is None:
        trip = Trip(org_id=org_id, trip_ref=data["trip_ref"])
        session.add(trip)
    trip.origin = data["origin"]
    trip.destination = data["destination"]
    trip.departs_at = data["departs_at"]
    trip.arrives_at = data.get("arrives_at")
    trip.fare = Decimal(data["fare"])
    trip.currency = data.get("currency") or "PKR"
    trip.seats_total = data.get("seats_total")
    trip.seats_available = data["seats_available"]
    trip.status = TripStatus(data.get("status") or TripStatus.SCHEDULED)
    trip.vehicle = data.get("vehicle")
    trip.notes = data.get("notes")
    await session.flush()
    return created


async def upcoming(session: AsyncSession, org_id: uuid.UUID) -> list[TripOut]:
    """Departures from an hour ago to two weeks ahead, soonest first."""
    now = datetime.now(UTC)
    rows = await session.scalars(
        select(Trip)
        .where(
            Trip.org_id == org_id,
            Trip.departs_at >= now - timedelta(hours=1),
            Trip.departs_at <= now + timedelta(days=UPCOMING_DAYS),
        )
        .order_by(Trip.departs_at)
        .limit(UPCOMING_LIMIT)
    )
    return [TripOut.model_validate(t) for t in rows]


async def count(session: AsyncSession, org_id: uuid.UUID) -> int:
    return len(list(await session.scalars(select(Trip.id).where(Trip.org_id == org_id))))
