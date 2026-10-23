"""Add a per-tenant hash chain to audit events.

Revision ID: a1f90dcb34e8
Revises: 5bc0c48f6a31
Create Date: 2026-10-24 09:15:00
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC

import sqlalchemy as sa
from alembic import op

revision: str = "a1f90dcb34e8"
down_revision: str | Sequence[str] | None = "5bc0c48f6a31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _digest(row: sa.Row, previous: str | None) -> str:
    body = {
        "org_id": str(row.org_id),
        "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "details": row.details,
        "occurred_at": row.occurred_at.astimezone(UTC).isoformat(),
        "previous_hash": previous,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("previous_hash", sa.String(64), nullable=True))
    op.add_column("audit_events", sa.Column("event_hash", sa.String(64), nullable=True))
    op.execute("DROP TRIGGER audit_events_append_only ON audit_events")

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("""
        SELECT id, org_id, actor_user_id, action, target_type, target_id, details, occurred_at
        FROM audit_events ORDER BY org_id, id
    """)
    ).fetchall()
    last_by_org: dict[str, str] = {}
    for row in rows:
        org = str(row.org_id)
        previous = last_by_org.get(org)
        digest = _digest(row, previous)
        connection.execute(
            sa.text(
                "UPDATE audit_events SET previous_hash=:previous, event_hash=:digest WHERE id=:id"
            ),
            {"previous": previous, "digest": digest, "id": row.id},
        )
        last_by_org[org] = digest

    op.alter_column("audit_events", "event_hash", nullable=False)
    op.execute("""
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_audit_change()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_append_only ON audit_events")
    op.drop_column("audit_events", "event_hash")
    op.drop_column("audit_events", "previous_hash")
    op.execute("""
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION reject_audit_change()
    """)
