"""Trip reads and writes other modules use (the import pipeline, the agents' research)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.transport.models import BookingStatus, SeatBooking, Trip, TripStatus
from agentflow.transport.schemas import TripOut

UPCOMING_DAYS = 14
UPCOMING_LIMIT = 60  # what the agents see: enough for a schedule, small enough for a prompt
BOOKING_HOLD = timedelta(minutes=20)


class BookingUnavailableError(Exception):
    pass


async def reserve(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    customer_ref: str,
    trip_id: uuid.UUID,
    seats: int,
    idempotency_key: uuid.UUID,
) -> tuple[SeatBooking, bool]:
    """Atomically place a short inventory hold. Caller must verify customer identity."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('booking:' || :key, 0))"),
        {"key": f"{org_id}:{idempotency_key}"},
    )
    existing = await session.scalar(
        select(SeatBooking).where(
            SeatBooking.org_id == org_id, SeatBooking.idempotency_key == idempotency_key
        )
    )
    if existing:
        if (
            existing.trip_id != trip_id
            or existing.customer_ref != customer_ref
            or existing.seats != seats
        ):
            raise BookingUnavailableError(
                "Idempotency key was already used for a different booking"
            )
        return existing, False
    if not 1 <= seats <= 6:
        raise BookingUnavailableError("Choose between 1 and 6 seats")
    trip = await session.scalar(
        select(Trip).where(Trip.org_id == org_id, Trip.id == trip_id).with_for_update()
    )
    now = datetime.now(UTC)
    if (
        trip is None
        or trip.status not in (TripStatus.SCHEDULED, TripStatus.DELAYED)
        or trip.departs_at <= now + timedelta(minutes=30)
    ):
        raise BookingUnavailableError("This trip is unavailable for booking")
    if trip.seats_available < seats:
        raise BookingUnavailableError("Not enough seats remain on this trip")
    trip.seats_available -= seats
    booking = SeatBooking(
        org_id=org_id,
        trip_id=trip.id,
        customer_ref=customer_ref,
        booking_ref=f"AF-{uuid.uuid4().hex[:10].upper()}",
        idempotency_key=idempotency_key,
        seats=seats,
        unit_fare=trip.fare,
        currency=trip.currency,
        status=BookingStatus.HELD,
        expires_at=now + BOOKING_HOLD,
    )
    session.add(booking)
    await session.flush()
    return booking, True


async def expire_holds(session: AsyncSession, *, limit: int = 200) -> int:
    """Release abandoned holds in bounded batches; lock the trip inventory as one unit."""
    now = datetime.now(UTC)
    holds = list(
        await session.scalars(
            select(SeatBooking)
            .where(SeatBooking.status == BookingStatus.HELD, SeatBooking.expires_at < now)
            .order_by(SeatBooking.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for hold in holds:
        trip = await session.scalar(
            select(Trip)
            .where(Trip.org_id == hold.org_id, Trip.id == hold.trip_id)
            .with_for_update()
        )
        if trip:
            trip.seats_available = min(
                trip.seats_total if trip.seats_total is not None else 2**31 - 1,
                trip.seats_available + hold.seats,
            )
        hold.status = BookingStatus.EXPIRED
        hold.expires_at = None
    return len(holds)


async def cancel_booking(session: AsyncSession, booking: SeatBooking) -> None:
    if booking.status not in (BookingStatus.HELD, BookingStatus.CONFIRMED):
        raise BookingUnavailableError("This booking can no longer be cancelled")
    trip = await session.scalar(
        select(Trip)
        .where(Trip.org_id == booking.org_id, Trip.id == booking.trip_id)
        .with_for_update()
    )
    if trip:
        trip.seats_available = min(
            trip.seats_total if trip.seats_total is not None else 2**31 - 1,
            trip.seats_available + booking.seats,
        )
    booking.status = BookingStatus.CANCELLED
    booking.expires_at = None


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
