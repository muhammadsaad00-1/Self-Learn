"""Write and read audit events."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.audit.models import AuditEvent
from agentflow.audit.schemas import AuditEventOut, AuditPage
from agentflow.identity.models import User


async def record(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | str,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit event to the caller's transaction.

    Deliberately no commit here: the event is saved only if the change it describes is saved.
    An audit log that can say "role changed" for a change that rolled back would be wrong.
    """
    session.add(
        AuditEvent(
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details or {},
        )
    )


async def list_events(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    action: str | None = None,
    before_id: int | None = None,
    limit: int = 50,
) -> AuditPage:
    """Newest first, `limit` per page. Pass the returned `next_before_id` to get the next page.

    Keyset pagination (`WHERE id < before_id`) rather than OFFSET: it stays fast on huge
    tables and doesn't skip or repeat rows while new events keep arriving.
    """
    query = (
        select(AuditEvent, User.email)
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .where(AuditEvent.org_id == org_id)
        .order_by(AuditEvent.id.desc())
        .limit(limit + 1)  # one extra row tells us whether another page exists
    )
    if action:
        query = query.where(AuditEvent.action == action)
    if before_id:
        query = query.where(AuditEvent.id < before_id)
    rows = (await session.execute(query)).all()
    events = [
        AuditEventOut(
            id=e.id,
            actor_user_id=e.actor_user_id,
            actor_email=email,
            action=e.action,
            target_type=e.target_type,
            target_id=e.target_id,
            details=e.details,
            occurred_at=e.occurred_at,
        )
        for e, email in rows[:limit]
    ]
    return AuditPage(events=events, next_before_id=events[-1].id if len(rows) > limit else None)
