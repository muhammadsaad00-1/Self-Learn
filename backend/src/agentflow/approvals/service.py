"""Approval use cases: request (by the worker), list, approve/reject (by a human)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.approvals.models import Approval, ApprovalStatus
from agentflow.audit import service as audit
from agentflow.workflow.models import Workflow, WorkflowStage, WorkflowStatus

APPROVAL_TTL = timedelta(hours=24)


class ApprovalNotFoundError(Exception):
    pass


class ApprovalClosedError(Exception):
    """Already approved, rejected or expired."""


class MakerCheckerError(Exception):
    """The person who submitted the request can't approve it (four-eyes principle)."""


async def request_approval(
    session: AsyncSession, workflow: Workflow, action: dict[str, Any], reasons: list[str]
) -> Approval:
    approval = Approval(
        org_id=workflow.org_id,
        workflow_id=workflow.id,
        action=action,
        policy_reasons=reasons,
        requested_by=workflow.created_by,
        expires_at=datetime.now(UTC) + APPROVAL_TTL,
    )
    session.add(approval)
    await session.flush()
    await audit.record(
        session,
        org_id=workflow.org_id,
        actor_user_id=None,
        action="approval.requested",
        target_type="approval",
        target_id=approval.id,
        details={"workflow_id": str(workflow.id), "reasons": reasons},
    )
    return approval


async def list_approvals(
    session: AsyncSession, org_id: uuid.UUID, status: ApprovalStatus | None, limit: int = 50
) -> list[tuple[Approval, Workflow]]:
    query = (
        select(Approval, Workflow)
        .join(Workflow, Workflow.id == Approval.workflow_id)
        .where(Approval.org_id == org_id)
        .order_by(Approval.requested_at.desc())
        .limit(limit)
    )
    if status is not None:
        query = query.where(Approval.status == status)
    return [(a, w) for a, w in (await session.execute(query)).all()]


async def get_for_workflow(session: AsyncSession, workflow_id: uuid.UUID) -> Approval | None:
    return await session.scalar(select(Approval).where(Approval.workflow_id == workflow_id))


async def decide(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    approval_id: uuid.UUID,
    actor_id: uuid.UUID,
    approve: bool,
    note: str | None,
) -> Approval:
    """Approve or reject, then re-queue the workflow for its execution stage.

    The row lock makes a decision final: two managers clicking at once can't both decide.
    """
    approval = await session.scalar(
        select(Approval)
        .where(Approval.id == approval_id, Approval.org_id == org_id)
        .with_for_update()
    )
    if approval is None:
        raise ApprovalNotFoundError
    if approval.status != ApprovalStatus.PENDING or approval.expires_at < datetime.now(UTC):
        raise ApprovalClosedError
    if approval.requested_by is not None and approval.requested_by == actor_id:
        raise MakerCheckerError

    approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    approval.decided_by = actor_id
    approval.decided_at = datetime.now(UTC)
    approval.note = note
    workflow = await session.scalar(
        select(Workflow).where(Workflow.id == approval.workflow_id).with_for_update()
    )
    if workflow is not None and workflow.status == WorkflowStatus.WAITING_APPROVAL:
        workflow.status = WorkflowStatus.QUEUED
        workflow.stage = WorkflowStage.EXECUTION
        workflow.available_at = datetime.now(UTC)
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=actor_id,
        action="approval.approved" if approve else "approval.rejected",
        target_type="approval",
        target_id=approval.id,
        details={"workflow_id": str(approval.workflow_id), "note": (note or "")[:200]},
    )
    return approval
