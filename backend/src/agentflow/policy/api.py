"""Admin API for deterministic, versioned tenant policy limits."""

import uuid
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from agentflow.db import SessionDep
from agentflow.identity.deps import AdminContextDep
from agentflow.policy import service
from agentflow.policy.engine import PolicyConfig

router = APIRouter(prefix="/v1/policy", tags=["policy"])


class PolicyLimits(BaseModel):
    auto_approve_limit: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    max_refund: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    max_reversals_per_day: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def max_must_cover_auto_limit(self) -> "PolicyLimits":
        if self.max_refund < self.auto_approve_limit:
            raise ValueError("Maximum refund must be at least the auto-approval limit")
        return self


class PolicyLimitsOut(PolicyLimits):
    org_id: uuid.UUID
    version: int


@router.get("/current", response_model=PolicyLimitsOut)
async def get_policy(ctx: AdminContextDep, session: SessionDep) -> PolicyLimitsOut:
    config, version = await service.get_config(session, ctx.org_id)
    return PolicyLimitsOut(
        org_id=ctx.org_id,
        version=version,
        auto_approve_limit=config.auto_approve_limit,
        max_refund=config.max_refund,
        max_reversals_per_day=config.max_reversals_per_day,
    )


@router.put("/current", response_model=PolicyLimitsOut)
async def update_policy(
    body: PolicyLimits, ctx: AdminContextDep, session: SessionDep
) -> PolicyLimitsOut:
    config = PolicyConfig(**body.model_dump())
    version = await service.set_config(
        session, org_id=ctx.org_id, actor_id=ctx.user.id, config=config
    )
    return PolicyLimitsOut(org_id=ctx.org_id, version=version, **body.model_dump())
