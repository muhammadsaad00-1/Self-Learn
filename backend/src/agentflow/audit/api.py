from typing import Annotated

from fastapi import APIRouter, Query

from agentflow.audit import service
from agentflow.audit.schemas import AuditPage
from agentflow.db import SessionDep
from agentflow.identity.deps import AdminContextDep

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("/events")
async def list_audit_events(
    ctx: AdminContextDep,
    session: SessionDep,
    action: Annotated[str | None, Query(max_length=64, examples=["member.added"])] = None,
    before_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    """The organization's audit trail, newest first. Admins and owners only."""
    return await service.list_events(
        session, ctx.org_id, action=action, before_id=before_id, limit=limit
    )
