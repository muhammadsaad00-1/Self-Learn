"""Write and read audit events."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.audit.models import AuditEvent
from agentflow.audit.schemas import AuditEventOut, AuditPage
from agentflow.config import get_settings
from agentflow.identity.models import User


def _event_hash(
    *,
    org_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any],
    occurred_at: datetime,
    previous_hash: str | None,
) -> str:
    body = {
        "org_id": str(org_id),
        "actor_user_id": str(actor_user_id) if actor_user_id else None,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "details": details,
        "occurred_at": occurred_at.astimezone(UTC).isoformat(),
        "previous_hash": previous_hash,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    # Keyed: without the app's secret nobody (DB superuser included) can forge a valid chain.
    key = get_settings().audit_chain_secret.get_secret_value().encode()
    return hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()


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
    A transaction-scoped per-tenant lock serializes chain append operations until commit.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('audit:' || :org_id, 0))"),
        {"org_id": str(org_id)},
    )
    previous_hash = await session.scalar(
        select(AuditEvent.event_hash)
        .where(AuditEvent.org_id == org_id)
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    occurred_at = datetime.now(UTC)
    target = str(target_id)
    safe_details = details or {}
    session.add(
        AuditEvent(
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target,
            details=safe_details,
            previous_hash=previous_hash,
            event_hash=_event_hash(
                org_id=org_id,
                actor_user_id=actor_user_id,
                action=action,
                target_type=target_type,
                target_id=target,
                details=safe_details,
                occurred_at=occurred_at,
                previous_hash=previous_hash,
            ),
            occurred_at=occurred_at,
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
            previous_hash=e.previous_hash,
            event_hash=e.event_hash,
            occurred_at=e.occurred_at,
        )
        for e, email in rows[:limit]
    ]
    return AuditPage(events=events, next_before_id=events[-1].id if len(rows) > limit else None)


async def verify_chain(session: AsyncSession, org_id: uuid.UUID) -> tuple[bool, int, int | None]:
    """Verify a tenant chain from its first event; report the first broken row if any."""
    rows = await session.stream_scalars(
        select(AuditEvent).where(AuditEvent.org_id == org_id).order_by(AuditEvent.id.asc())
    )
    previous_hash: str | None = None
    verified = 0
    try:
        async for event in rows:
            expected = _event_hash(
                org_id=event.org_id,
                actor_user_id=event.actor_user_id,
                action=event.action,
                target_type=event.target_type,
                target_id=event.target_id,
                details=event.details,
                occurred_at=event.occurred_at,
                previous_hash=previous_hash,
            )
            if event.previous_hash != previous_hash or not hmac.compare_digest(
                event.event_hash, expected
            ):
                return False, verified, event.id
            previous_hash = event.event_hash
            verified += 1
    finally:
        await rows.close()  # a server-side cursor stays open until closed, even on early return
    return True, verified, None
