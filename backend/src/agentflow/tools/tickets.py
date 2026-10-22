"""The ticket tool: open one support ticket for a workflow, exactly once.

Same contract as the refund tool: tenant-scoped session (caller), idempotency through the
action ledger (a retried workflow returns the ticket it already opened), audit event, all in
the caller's transaction.
"""

import hashlib
import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.audit import service as audit
from agentflow.tickets.models import Ticket, TicketPriority
from agentflow.tools.models import ActionExecution

TOOL = "open_ticket"


class TicketRef(BaseModel):
    id: uuid.UUID
    code: str  # e.g. TCK-1042, quotable to the customer
    priority: TicketPriority
    reasons: list[str]


def _key(workflow_id: uuid.UUID) -> str:
    return hashlib.sha256(f"{workflow_id}:{TOOL}:v1".encode()).hexdigest()


async def open_ticket(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    workflow_id: uuid.UUID,
    reasons: list[str],
    state: dict[str, Any],
) -> TicketRef:
    key = _key(workflow_id)
    previous = await session.scalar(
        select(ActionExecution).where(
            ActionExecution.org_id == org_id, ActionExecution.idempotency_key == key
        )
    )
    if previous is not None:
        return TicketRef.model_validate(previous.result)

    triage = state.get("triage") or {}
    proposal = state.get("proposal") or {}
    said = state.get("customer_message") or state.get("message", "")
    # The criticality analysis may have raised the priority above triage's urgency.
    urgency = (state.get("risk") or {}).get("priority") or triage.get("urgency") or "normal"
    category = triage.get("category")
    lines = [
        "Why a human is needed:",
        *[f"- {r}" for r in reasons],
        "",
        f"Customer message: {said}",
        f"Customer: {state.get('customer_ref') or 'unknown'}",
    ]
    if signals := (state.get("risk") or {}).get("signals"):
        lines.append("Risk signals: " + "; ".join(f"{s['id']} {s['detail']}" for s in signals))
    if proposal:
        lines.append(f"Agents proposed: {proposal.get('action')} ({proposal.get('rationale')})")
    ticket = Ticket(
        org_id=org_id,
        workflow_id=workflow_id,
        title=(triage.get("title") or triage.get("summary") or said)[:300],
        description="\n".join(lines),
        reasons=reasons,
        customer_message=said,
        priority=TicketPriority(urgency),
        category=category,
        customer_ref=state.get("customer_ref"),
    )
    session.add(ticket)
    await session.flush()
    ref = TicketRef(id=ticket.id, code=ticket.code, priority=ticket.priority, reasons=reasons)
    session.add(
        ActionExecution(
            org_id=org_id,
            workflow_id=workflow_id,
            tool=TOOL,
            idempotency_key=key,
            request={"reasons": reasons},
            result=ref.model_dump(mode="json"),
        )
    )
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=None,
        action="tool.ticket_opened",
        target_type="ticket",
        target_id=ticket.id,
        details={"code": ref.code, "priority": ref.priority, "workflow_id": str(workflow_id)},
    )
    return ref
