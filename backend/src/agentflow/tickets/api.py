"""Ticket queue for the operations team: list, take, resolve."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from agentflow.audit import service as audit
from agentflow.db import SessionDep
from agentflow.identity.deps import OrgContextDep
from agentflow.tickets.models import Ticket, TicketPriority, TicketStatus

router = APIRouter(prefix="/v1/tickets", tags=["tickets"])

_PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    workflow_id: uuid.UUID | None
    title: str
    description: str
    priority: TicketPriority
    category: str | None
    customer_ref: str | None
    status: TicketStatus
    assignee_id: uuid.UUID | None
    resolution_note: str | None
    created_at: datetime
    resolved_at: datetime | None


class TicketUpdate(BaseModel):
    action: Literal["take", "resolve", "reopen"]
    note: str | None = Field(default=None, max_length=2000)


@router.get("")
async def list_tickets(
    ctx: OrgContextDep,
    session: SessionDep,
    status_filter: Annotated[Literal["open", "resolved", "all"], Query(alias="status")] = "open",
) -> list[TicketOut]:
    """Open tickets (default) most urgent first, or resolved/all newest first."""
    query = select(Ticket).where(Ticket.org_id == ctx.org_id).order_by(Ticket.created_at.desc())
    if status_filter == "open":
        query = query.where(Ticket.status != TicketStatus.RESOLVED)
    elif status_filter == "resolved":
        query = query.where(Ticket.status == TicketStatus.RESOLVED)
    tickets = [TicketOut.model_validate(t) for t in await session.scalars(query.limit(200))]
    if status_filter == "open":
        tickets.sort(key=lambda t: _PRIORITY_ORDER[t.priority])  # stable: newest first per level
    return tickets


@router.patch("/{ticket_id}")
async def update_ticket(
    ticket_id: uuid.UUID, body: TicketUpdate, ctx: OrgContextDep, session: SessionDep
) -> TicketOut:
    """take: assign to me (in progress) · resolve: close with a note · reopen."""
    ticket = await session.scalar(
        select(Ticket).where(Ticket.id == ticket_id, Ticket.org_id == ctx.org_id).with_for_update()
    )
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    if body.action == "take":
        ticket.status, ticket.assignee_id = TicketStatus.IN_PROGRESS, ctx.user.id
    elif body.action == "resolve":
        if not (body.note or "").strip():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "A resolution note is required"
            )
        ticket.status, ticket.resolution_note = TicketStatus.RESOLVED, body.note
        ticket.resolved_at = datetime.now(UTC)
        ticket.assignee_id = ticket.assignee_id or ctx.user.id
    else:
        ticket.status, ticket.resolved_at = TicketStatus.OPEN, None
    await audit.record(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action=f"ticket.{body.action}",
        target_type="ticket",
        target_id=ticket.id,
        details={"code": ticket.code},
    )
    await session.flush()
    await session.refresh(ticket)
    return TicketOut.model_validate(ticket)
