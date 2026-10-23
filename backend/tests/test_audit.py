"""Audit trail: what gets recorded, who can read it, and that it can't be changed or faked."""

import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.audit import service as audit
from agentflow.config import Settings
from agentflow.db import SessionDep, set_tenant_context
from agentflow.identity.models import Organization
from agentflow.main import create_app
from tests.helpers import Account, signup


def events(client: TestClient, account: Account, **params: str | int) -> list[dict[str, object]]:
    resp = client.get("/v1/audit/events", headers=account.headers(), params=params)
    assert resp.status_code == 200, resp.text
    result: list[dict[str, object]] = resp.json()["events"]
    return result


def test_membership_changes_are_recorded_newest_first(client: TestClient) -> None:
    owner, joiner = signup(client), signup(client)
    client.post("/v1/members", headers=owner.headers(), json={"email": joiner.email})
    client.patch(f"/v1/members/{joiner.user_id}", headers=owner.headers(), json={"role": "admin"})
    client.delete(f"/v1/members/{joiner.user_id}", headers=owner.headers())

    trail = events(client, owner)
    assert [(e["action"], e["details"]) for e in trail] == [
        ("member.removed", {"role": "admin"}),
        ("member.role_changed", {"from": "member", "to": "admin"}),
        ("member.added", {"role": "member"}),
        ("org.created", {"via": "signup"}),
    ]
    assert {e["actor_email"] for e in trail} == {owner.email}
    assert trail[0]["target_id"] == joiner.user_id


def test_filter_and_paginate(client: TestClient) -> None:
    owner = signup(client)
    for _ in range(3):
        extra = signup(client)
        client.post("/v1/members", headers=owner.headers(), json={"email": extra.email})

    page1 = client.get("/v1/audit/events", headers=owner.headers(), params={"limit": 2}).json()
    page2 = client.get(
        "/v1/audit/events",
        headers=owner.headers(),
        params={"limit": 2, "before_id": page1["next_before_id"]},
    ).json()
    assert len(page1["events"]) == len(page2["events"]) == 2
    assert page2["next_before_id"] is None  # 4 events in total: 3 added + org.created
    assert len(events(client, owner, action="member.added")) == 3


def test_members_cannot_read_audit_trail(client: TestClient) -> None:
    owner, joiner = signup(client), signup(client)
    client.post("/v1/members", headers=owner.headers(), json={"email": joiner.email})
    as_member = joiner.headers(org_id=owner.org_id)
    assert client.get("/v1/audit/events", headers=as_member).status_code == 403


def test_each_org_sees_only_its_own_trail(client: TestClient) -> None:
    alice, bob = signup(client), signup(client)
    assert [e["target_id"] for e in events(client, alice)] == [alice.org_id]
    assert [e["target_id"] for e in events(client, bob)] == [bob.org_id]


def test_event_rolls_back_with_failed_change(settings: Settings) -> None:
    """The audit event and the change share one transaction: no change, no event."""
    app = create_app(settings)

    @app.post("/_test/fail-after-audit/{org_id}")
    async def fail_after_audit(org_id: uuid.UUID, session: SessionDep) -> None:
        await set_tenant_context(session, org_id=org_id)
        await audit.record(
            session,
            org_id=org_id,
            actor_user_id=None,
            action="test.must_not_persist",
            target_type="test",
            target_id="x",
        )
        raise HTTPException(409, "the change itself failed")

    with TestClient(app) as client:
        owner = signup(client)
        assert client.post(f"/_test/fail-after-audit/{owner.org_id}").status_code == 409
        assert events(client, owner, action="test.must_not_persist") == []


# --- Database-level immutability -------------------------------------------------------


async def insert_event(session: AsyncSession) -> uuid.UUID:
    org_id = uuid.uuid4()
    await set_tenant_context(session, org_id=org_id)
    session.add(Organization(id=org_id, name="Acme", slug=org_id.hex))
    await session.flush()
    await audit.record(
        session, org_id=org_id, actor_user_id=None, action="x.y", target_type="t", target_id="1"
    )
    await session.flush()
    return org_id


@pytest.mark.parametrize(
    "sql", ["UPDATE audit_events SET action = 'forged'", "DELETE FROM audit_events"]
)
async def test_app_role_has_no_update_or_delete_privilege(
    app_session: AsyncSession, sql: str
) -> None:
    await insert_event(app_session)
    with pytest.raises(DBAPIError, match="permission denied"):
        await app_session.execute(text(sql))


@pytest.mark.parametrize(
    "sql", ["UPDATE audit_events SET action = 'forged'", "DELETE FROM audit_events"]
)
async def test_even_the_owner_cannot_rewrite_history(db_session: AsyncSession, sql: str) -> None:
    await insert_event(db_session)
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(text(sql))


async def test_superuser_rewrite_with_recomputed_hashes_is_detected(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id = await insert_event(db_session)
    for action in ("refund.executed", "approval.decided"):
        await audit.record(
            db_session,
            org_id=org_id,
            actor_user_id=None,
            action=action,
            target_type="t",
            target_id="1",
        )
    await db_session.flush()
    assert await audit.verify_chain(db_session, org_id) == (True, 3, None)

    # The attacker is a DB superuser: skips the append-only trigger, rewrites "refund.executed"
    # and recomputes a perfectly consistent chain, but with a key of their own.
    rows = (
        await db_session.execute(
            text("SELECT * FROM audit_events WHERE org_id = :o ORDER BY id"), {"o": org_id}
        )
    ).all()
    await db_session.execute(text("SET LOCAL session_replication_role = replica"))
    with monkeypatch.context() as m:
        m.setattr(audit, "get_settings", lambda: Settings(audit_chain_secret="attacker-guess"))
        previous = rows[0].event_hash
        for row in rows[1:]:
            action = "nothing.happened" if row.action == "refund.executed" else row.action
            forged = audit._event_hash(
                org_id=org_id,
                actor_user_id=None,
                action=action,
                target_type=row.target_type,
                target_id=row.target_id,
                details=row.details,
                occurred_at=row.occurred_at,
                previous_hash=previous,
            )
            await db_session.execute(
                text(
                    "UPDATE audit_events SET action=:a, previous_hash=:p, event_hash=:h "
                    "WHERE id=:id"
                ),
                {"a": action, "p": previous, "h": forged, "id": row.id},
            )
            previous = forged
    db_session.expire_all()
    assert await audit.verify_chain(db_session, org_id) == (False, 1, rows[1].id)
