"""Read and update versioned per-organization policy limits."""

import uuid
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.audit import service as audit
from agentflow.policy.engine import DEFAULT_CONFIG, PolicyConfig
from agentflow.policy.models import OrgPolicy


async def get_config(session: AsyncSession, org_id: uuid.UUID) -> tuple[PolicyConfig, int]:
    row = await session.get(OrgPolicy, org_id)
    if row is None:
        return DEFAULT_CONFIG, 0
    return (
        PolicyConfig(
            auto_approve_limit=row.auto_approve_limit,
            max_refund=row.max_refund,
            max_reversals_per_day=row.max_reversals_per_day,
        ),
        row.version,
    )


async def set_config(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    config: PolicyConfig,
) -> int:
    # Serialize first-time writes too: a row lock cannot lock a row that does not exist.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('policy:' || :org_id, 0))"),
        {"org_id": str(org_id)},
    )
    row = await session.scalar(
        select(OrgPolicy).where(OrgPolicy.org_id == org_id).with_for_update()
    )
    if row is None:
        row = OrgPolicy(
            org_id=org_id,
            auto_approve_limit=config.auto_approve_limit,
            max_refund=config.max_refund,
            max_reversals_per_day=config.max_reversals_per_day,
            version=1,
        )
        session.add(row)
    else:
        row.auto_approve_limit = config.auto_approve_limit
        row.max_refund = config.max_refund
        row.max_reversals_per_day = config.max_reversals_per_day
        row.version += 1
    await session.flush()
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=actor_id,
        action="policy.updated",
        target_type="policy",
        target_id="limits",
        details={
            "version": row.version,
            "auto_approve_limit": str(Decimal(config.auto_approve_limit)),
            "max_refund": str(Decimal(config.max_refund)),
            "max_reversals_per_day": config.max_reversals_per_day,
        },
    )
    return row.version
