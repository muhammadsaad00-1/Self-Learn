import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status

from agentflow.db import SessionDep
from agentflow.identity.deps import OrgContextDep
from agentflow.observability import request_id_var
from agentflow.workflow import service
from agentflow.workflow.schemas import (
    CreateWorkflowRequest,
    WorkflowOut,
    WorkflowStepOut,
    WorkflowSummary,
)

router = APIRouter(prefix="/v1/workflows", tags=["workflows"])

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
        description="Unique per logical request (e.g. a UUID). Retrying with the same key "
        "and body returns the original workflow instead of creating another.",
    ),
]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_workflow(
    body: CreateWorkflowRequest,
    idempotency_key: IdempotencyKey,
    ctx: OrgContextDep,
    session: SessionDep,
    response: Response,
) -> WorkflowOut:
    """Queue a customer request for the agents. 202: accepted, processing happens in the
    worker; poll GET /v1/workflows/{id}. A replay of the same key answers 200."""
    try:
        wf, created = await service.create(
            session,
            org_id=ctx.org_id,
            user_id=ctx.user.id,
            idempotency_key=idempotency_key,
            body=body,
            request_id=request_id_var.get(),
        )
    except service.IdempotencyConflictError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Idempotency-Key was already used for a different request"
        ) from None
    if not created:
        response.status_code = status.HTTP_200_OK
    await session.flush()
    await session.refresh(wf)  # server-set columns (created_at, available_at)
    return WorkflowOut.model_validate(wf)


@router.get("")
async def list_workflows(ctx: OrgContextDep, session: SessionDep) -> list[WorkflowSummary]:
    """The 50 most recent workflows in the organization."""
    return await service.list_recent(session, ctx.org_id)


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: uuid.UUID, ctx: OrgContextDep, session: SessionDep
) -> WorkflowOut:
    """Status, triage result and every executed step with its model, tokens and latency."""
    try:
        wf = await service.get(session, ctx.org_id, workflow_id)
    except service.WorkflowNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found") from None
    out = WorkflowOut.model_validate(wf)
    out.steps = [WorkflowStepOut.model_validate(s) for s in await service.steps_of(session, wf.id)]
    return out
