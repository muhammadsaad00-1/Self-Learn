"""Ticket queue for the operations team: list, take, hand off to a teammate, resolve.

Customers see their own cases (code, status) through the customer API, and a resolution
can include a message that is posted into the customer's chat."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from agentflow.audit import service as audit
from agentflow.conversations import service as conversations
from agentflow.conversations.models import IssueStatus
from agentflow.db import SessionDep
from agentflow.identity import service as identity
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
    activity: list[dict[str, Any]]


class TicketUpdate(BaseModel):
    action: Literal["take", "assign", "note", "resolve", "reopen"]
    note: str | None = Field(default=None, max_length=2000)
    assignee_id: uuid.UUID | None = None  # for assign: a member of this org
    # For resolve: posted into the customer's chat as a message from the team.
    customer_message: str | None = Field(default=None, max_length=2000)


@router.get("")
async def list_tickets(
    ctx: OrgContextDep,
    session: SessionDep,
    status_filter: Annotated[Literal["open", "resolved", "all"], Query(alias="status")] = "open",
    mine: bool = False,
) -> list[TicketOut]:
    """Open tickets (default) most urgent first, or resolved/all newest first.
    `mine=true`: only tickets assigned to me."""
    query = select(Ticket).where(Ticket.org_id == ctx.org_id).order_by(Ticket.created_at.desc())
    if mine:
        query = query.where(Ticket.assignee_id == ctx.user.id)
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
    """take: assign to me · assign: hand off to a teammate (note recommended) · note: add a
    note · resolve: close with a note (and optionally message the customer) · reopen."""
    ticket = await session.scalar(
        select(Ticket).where(Ticket.id == ticket_id, Ticket.org_id == ctx.org_id).with_for_update()
    )
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    note = (body.note or "").strip() or None
    event: dict[str, Any] = {
        "at": datetime.now(UTC).isoformat(),
        "by": ctx.user.full_name,
        "kind": body.action,
        "text": note,
    }
    if body.action == "take":
        ticket.status, ticket.assignee_id = TicketStatus.IN_PROGRESS, ctx.user.id
    elif body.action == "assign":
        members = {m.user_id: m for m in await identity.list_members(session, ctx.org_id)}
        if body.assignee_id not in members:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Assignee must be a member of this company"
            )
        ticket.status, ticket.assignee_id = TicketStatus.IN_PROGRESS, body.assignee_id
        event["to"] = members[body.assignee_id].full_name
    elif body.action == "note":
        if not note:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The note is empty")
    elif body.action == "resolve":
        if not note:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "A resolution note is required"
            )
        ticket.status, ticket.resolution_note = TicketStatus.RESOLVED, note
        ticket.resolved_at = datetime.now(UTC)
        ticket.assignee_id = ticket.assignee_id or ctx.user.id
        customer_message = (body.customer_message or "").strip() or None
        if ticket.customer_ref:
            await conversations.ticket_resolved(
                session,
                org_id=ctx.org_id,
                ticket_id=ticket.id,
                customer_ref=ticket.customer_ref,
                user_id=ctx.user.id,
                customer_message=customer_message,
                card={"case": {"code": ticket.code, "priority": ticket.priority, "resolved": True}},
            )
        if customer_message:
            event["customer_message"] = customer_message
    else:
        ticket.status, ticket.resolved_at = TicketStatus.OPEN, None
        if issue := await conversations.issue_for_ticket(session, ticket.id):
            issue.status = IssueStatus.WITH_SPECIALIST
    ticket.activity = [*ticket.activity, event]  # new list: JSONB changes need reassignment
    await audit.record(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action=f"ticket.{body.action}",
        target_type="ticket",
        target_id=ticket.id,
        details={"code": ticket.code, **{k: v for k, v in event.items() if k in ("to", "text")}},
    )
    await session.flush()
    await session.refresh(ticket)
    return TicketOut.model_validate(ticket)
