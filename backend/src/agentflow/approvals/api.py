import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.approvals import service
from agentflow.approvals.models import Approval, ApprovalStatus
from agentflow.db import SessionDep
from agentflow.identity.deps import AdminContextDep, OrgContext
from agentflow.workflow.models import Workflow

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


class ApprovalOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    status: ApprovalStatus
    action: dict[str, Any]
    policy_reasons: list[str]
    requested_by: uuid.UUID | None
    requested_at: datetime
    expires_at: datetime
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    note: str | None
    message_preview: str
    customer_ref: str | None
    category: str | None
    urgency: str | None


class DecisionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _out(approval: Approval, workflow: Workflow) -> ApprovalOut:
    triage = (workflow.result or {}).get("triage") or {}
    return ApprovalOut(
        id=approval.id,
        workflow_id=approval.workflow_id,
        status=approval.status,
        action=approval.action,
        policy_reasons=approval.policy_reasons,
        requested_by=approval.requested_by,
        requested_at=approval.requested_at,
        expires_at=approval.expires_at,
        decided_by=approval.decided_by,
        decided_at=approval.decided_at,
        note=approval.note,
        message_preview=workflow.input["message"][:160],
        customer_ref=workflow.input.get("customer_ref"),
        category=triage.get("category"),
        urgency=triage.get("urgency"),
    )


@router.get("")
async def list_approvals(
    ctx: AdminContextDep,
    session: SessionDep,
    status_filter: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
) -> list[ApprovalOut]:
    """Approvals in this organization, newest first (admins and owners)."""
    rows = await service.list_approvals(session, ctx.org_id, status_filter)
    return [_out(a, w) for a, w in rows]


async def _decide(
    approval_id: uuid.UUID,
    approve: bool,
    body: DecisionRequest,
    ctx: OrgContext,
    session: AsyncSession,
) -> ApprovalOut:
    try:
        approval = await service.decide(
            session,
            org_id=ctx.org_id,
            approval_id=approval_id,
            actor_id=ctx.user.id,
            approve=approve,
            note=body.note,
        )
    except service.ApprovalNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found") from None
    except service.ApprovalClosedError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This approval was already decided or has expired"
        ) from None
    except service.MakerCheckerError:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Maker-checker: you submitted this request, so another officer must decide it",
        ) from None
    workflow = await session.get(Workflow, approval.workflow_id)
    assert workflow is not None  # noqa: S101 (FK guarantees it)
    return _out(approval, workflow)


@router.post("/{approval_id}/approve")
async def approve(
    approval_id: uuid.UUID, body: DecisionRequest, ctx: AdminContextDep, session: SessionDep
) -> ApprovalOut:
    """Approve the proposed action; the workflow resumes and executes it (re-authorized)."""
    return await _decide(approval_id, True, body, ctx, session)


@router.post("/{approval_id}/reject")
async def reject(
    approval_id: uuid.UUID, body: DecisionRequest, ctx: AdminContextDep, session: SessionDep
) -> ApprovalOut:
    """Reject the proposed action; the workflow resumes without executing it."""
    return await _decide(approval_id, False, body, ctx, session)
