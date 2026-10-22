"""Ticket reads other modules use (the customer API)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.tickets.models import Ticket


async def for_customer(session: AsyncSession, org_id: uuid.UUID, customer_ref: str) -> list[Ticket]:
    rows = await session.scalars(
        select(Ticket)
        .where(Ticket.org_id == org_id, Ticket.customer_ref == customer_ref)
        .order_by(Ticket.created_at.desc())
        .limit(50)
    )
    return list(rows)


async def codes(session: AsyncSession, ticket_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Ticket id -> case code (TCK-1042), for showing cases next to conversations."""
    if not ticket_ids:
        return {}
    rows = await session.scalars(select(Ticket).where(Ticket.id.in_(ticket_ids)))
    return {t.id: t.code for t in rows}
