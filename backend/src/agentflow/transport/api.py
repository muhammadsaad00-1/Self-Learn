"""Seat reservations for customers and authorized staff."""

import hashlib
import hmac
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.audit import service as audit
from agentflow.conversations.api import CustomerDep
from agentflow.db import SessionDep
from agentflow.identity.deps import AdminContextDep, SettingsDep
from agentflow.ledger.models import Account
from agentflow.transport import service
from agentflow.transport.models import BookingStatus, SeatBooking, Trip
from agentflow.transport.schemas import TripOut

customer_router = APIRouter(prefix="/v1/customer/bookings", tags=["bookings"])
admin_router = APIRouter(prefix="/v1/bookings", tags=["bookings"])


class BookingIn(BaseModel):
    trip_id: uuid.UUID
    phone: str = Field(min_length=11, max_length=20)
    cnic: str = Field(min_length=13, max_length=32)
    seats: int = Field(ge=1, le=6)
    idempotency_key: uuid.UUID


class AdminBookingIn(BookingIn):
    customer_ref: str = Field(min_length=1, max_length=100)


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trip_id: uuid.UUID
    trip_ref: str | None = None
    customer_ref: str
    booking_ref: str
    seats: int
    unit_fare: Decimal
    currency: str
    status: BookingStatus
    expires_at: datetime | None
    created_at: datetime


def _phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return "92" + digits[1:] if digits.startswith("0") else digits


def _matches(account: Account, phone: str, cnic: str, org_id: uuid.UUID, secret: str) -> bool:
    digits = re.sub(r"\D", "", cnic)
    digest = hmac.new(secret.encode(), f"{org_id}:{digits}".encode(), hashlib.sha256).hexdigest()
    return bool(
        len(digits) == 13
        and account.phone
        and hmac.compare_digest(account.phone, _phone(phone))
        and account.cnic_digest
        and hmac.compare_digest(account.cnic_digest, digest)
    )


async def _reserve(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    customer_ref: str,
    body: BookingIn,
    secret: str,
    actor_id: uuid.UUID | None,
) -> BookingOut:
    account = await session.scalar(
        select(Account).where(Account.org_id == org_id, Account.customer_ref == customer_ref)
    )
    if account is None or not _matches(account, body.phone, body.cnic, org_id, secret):
        # Avoid revealing which identity field failed.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Customer identity could not be verified")
    try:
        booking, created = await service.reserve(
            session,
            org_id=org_id,
            customer_ref=customer_ref,
            trip_id=body.trip_id,
            seats=body.seats,
            idempotency_key=body.idempotency_key,
        )
    except service.BookingUnavailableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    if created:
        await audit.record(
            session,
            org_id=org_id,
            actor_user_id=actor_id,
            action="booking.held",
            target_type="seat_booking",
            target_id=booking.id,
            details={
                "trip_id": str(booking.trip_id),
                "seats": booking.seats,
                "booking_ref": booking.booking_ref,
            },
        )
    trip = await session.get(Trip, booking.trip_id)
    return BookingOut.model_validate(booking).model_copy(
        update={"trip_ref": trip.trip_ref if trip else None}
    )


@customer_router.post("", status_code=status.HTTP_201_CREATED)
async def customer_book(
    body: BookingIn,
    me: CustomerDep,
    session: SessionDep,
    settings: SettingsDep,
) -> BookingOut:
    return await _reserve(
        session,
        org_id=me.org_id,
        customer_ref=me.customer_ref,
        body=body,
        secret=settings.cnic_hash_secret.get_secret_value(),
        actor_id=None,
    )


@customer_router.get("/trips")
async def customer_trips(me: CustomerDep, session: SessionDep) -> list[TripOut]:
    return await service.upcoming(session, me.org_id)


@customer_router.get("")
async def customer_bookings(me: CustomerDep, session: SessionDep) -> list[BookingOut]:
    rows = list(
        await session.scalars(
            select(SeatBooking)
            .where(SeatBooking.org_id == me.org_id, SeatBooking.customer_ref == me.customer_ref)
            .order_by(SeatBooking.created_at.desc())
            .limit(50)
        )
    )
    trips = (
        {
            trip.id: trip.trip_ref
            for trip in await session.scalars(
                select(Trip).where(Trip.id.in_([row.trip_id for row in rows]))
            )
        }
        if rows
        else {}
    )
    return [
        BookingOut.model_validate(row).model_copy(update={"trip_ref": trips.get(row.trip_id)})
        for row in rows
    ]


@customer_router.post("/{booking_id}/cancel")
async def customer_cancel(
    booking_id: uuid.UUID,
    me: CustomerDep,
    session: SessionDep,
) -> dict[str, str]:
    booking = await session.scalar(
        select(SeatBooking)
        .where(
            SeatBooking.id == booking_id,
            SeatBooking.org_id == me.org_id,
            SeatBooking.customer_ref == me.customer_ref,
        )
        .with_for_update()
    )
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    try:
        await service.cancel_booking(session, booking)
    except service.BookingUnavailableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    await audit.record(
        session,
        org_id=me.org_id,
        actor_user_id=None,
        action="booking.cancelled",
        target_type="seat_booking",
        target_id=booking.id,
    )
    return {"status": booking.status.value}


@admin_router.get("")
async def list_bookings(
    ctx: AdminContextDep,
    session: SessionDep,
    customer_ref: Annotated[str | None, Query(max_length=100)] = None,
) -> list[BookingOut]:
    query = select(SeatBooking).where(SeatBooking.org_id == ctx.org_id)
    if customer_ref:
        query = query.where(SeatBooking.customer_ref == customer_ref)
    rows = list(await session.scalars(query.order_by(SeatBooking.created_at.desc()).limit(200)))
    trips = (
        {
            trip.id: trip.trip_ref
            for trip in await session.scalars(
                select(Trip).where(Trip.id.in_([row.trip_id for row in rows]))
            )
        }
        if rows
        else {}
    )
    return [
        BookingOut.model_validate(row).model_copy(update={"trip_ref": trips.get(row.trip_id)})
        for row in rows
    ]


@admin_router.get("/trips")
async def admin_trips(ctx: AdminContextDep, session: SessionDep) -> list[TripOut]:
    return await service.upcoming(session, ctx.org_id)


@admin_router.post("", status_code=status.HTTP_201_CREATED)
async def admin_book(
    body: AdminBookingIn,
    ctx: AdminContextDep,
    session: SessionDep,
    settings: SettingsDep,
) -> BookingOut:
    return await _reserve(
        session,
        org_id=ctx.org_id,
        customer_ref=body.customer_ref,
        body=body,
        secret=settings.cnic_hash_secret.get_secret_value(),
        actor_id=ctx.user.id,
    )


@admin_router.post("/{booking_id}/cancel")
async def admin_cancel(
    booking_id: uuid.UUID,
    ctx: AdminContextDep,
    session: SessionDep,
) -> dict[str, str]:
    booking = await session.scalar(
        select(SeatBooking)
        .where(SeatBooking.id == booking_id, SeatBooking.org_id == ctx.org_id)
        .with_for_update()
    )
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    try:
        await service.cancel_booking(session, booking)
    except service.BookingUnavailableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    await audit.record(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="booking.cancelled",
        target_type="seat_booking",
        target_id=booking.id,
    )
    return {"status": booking.status.value}


@admin_router.post("/{booking_id}/confirm")
async def admin_confirm(
    booking_id: uuid.UUID,
    ctx: AdminContextDep,
    session: SessionDep,
) -> dict[str, str]:
    booking = await session.scalar(
        select(SeatBooking)
        .where(SeatBooking.id == booking_id, SeatBooking.org_id == ctx.org_id)
        .with_for_update()
    )
    if booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    if booking.status != BookingStatus.HELD:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an active hold can be confirmed")
    if booking.expires_at is None or booking.expires_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_409_CONFLICT, "This seat hold has expired")
    booking.status = BookingStatus.CONFIRMED
    booking.expires_at = None
    await audit.record(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="booking.confirmed",
        target_type="seat_booking",
        target_id=booking.id,
    )
    return {"status": booking.status.value}
