"""Re-sign the audit hash chain with an HMAC keyed outside the database.

A plain SHA-256 chain can be recomputed by anyone who can edit the table (a DB superuser),
so edits would still verify. The key now lives in the app's environment (Secrets Manager on
AWS), which the database never sees.

Revision ID: 3c7e1b9d0a42
Revises: 7ea0d2c48f15
Create Date: 2026-10-24 11:00:00
"""

import hashlib
import hmac
import json
from collections.abc import Sequence
from datetime import UTC

import sqlalchemy as sa
from alembic import op

from agentflow.config import get_settings

revision: str = "3c7e1b9d0a42"
down_revision: str | Sequence[str] | None = "7ea0d2c48f15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rechain(sign: object) -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("""
        SELECT id, org_id, actor_user_id, action, target_type, target_id, details, occurred_at
        FROM audit_events ORDER BY org_id, id
    """)
    ).fetchall()
    op.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only")
    last_by_org: dict[str, str] = {}
    for row in rows:
        org = str(row.org_id)
        previous = last_by_org.get(org)
        body = {
            "org_id": org,
            "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "details": row.details,
            "occurred_at": row.occurred_at.astimezone(UTC).isoformat(),
            "previous_hash": previous,
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
        digest = sign(raw)  # type: ignore[operator]
        connection.execute(
            sa.text("UPDATE audit_events SET previous_hash=:p, event_hash=:d WHERE id=:id"),
            {"p": previous, "d": digest, "id": row.id},
        )
        last_by_org[org] = digest
    op.execute("ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only")


def upgrade() -> None:
    key = get_settings().audit_chain_secret.get_secret_value().encode()
    _rechain(lambda raw: hmac.new(key, raw, hashlib.sha256).hexdigest())


def downgrade() -> None:
    _rechain(lambda raw: hashlib.sha256(raw).hexdigest())
