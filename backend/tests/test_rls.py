"""Row-level security, tested directly in Postgres as the app's low-privilege role.

These queries deliberately leave out `WHERE org_id = ...`: they simulate an application
bug, and show the database still refuses to return or write another tenant's rows.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.db import set_tenant_context
from agentflow.identity.models import Membership, Organization, Role, User

ORG_A, ORG_B = uuid.uuid4(), uuid.uuid4()
ALICE, BOB, CAROL = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()  # Carol is in both orgs


async def seed(session: AsyncSession) -> None:
    """Two tenants, inserted the way the app does: acting inside each tenant in turn."""
    for name, uid in (("alice", ALICE), ("bob", BOB), ("carol", CAROL)):
        session.add(User(id=uid, email=f"{name}-{uid.hex[:6]}@t.example", full_name=name))
    for org_id, owner in ((ORG_A, ALICE), (ORG_B, BOB)):
        await set_tenant_context(session, org_id=org_id)
        session.add(Organization(id=org_id, name=str(org_id), slug=org_id.hex))
        await session.flush()
        session.add(Membership(org_id=org_id, user_id=owner, role=Role.OWNER))
        session.add(Membership(org_id=org_id, user_id=CAROL, role=Role.MEMBER))
        await session.flush()


async def clear_context(session: AsyncSession) -> None:
    for name in ("app.current_org_id", "app.current_user_id"):
        await session.execute(text("SELECT set_config(:n, '', true)"), {"n": name})


async def visible(session: AsyncSession, sql: str) -> set[str]:
    return {str(v) for v in (await session.scalars(text(sql))).all()}


async def test_no_context_sees_no_tenant_rows(app_session: AsyncSession) -> None:
    await seed(app_session)
    await clear_context(app_session)
    assert await visible(app_session, "SELECT id FROM organizations") == set()
    assert await visible(app_session, "SELECT org_id FROM memberships") == set()


async def test_org_context_sees_only_that_org(app_session: AsyncSession) -> None:
    await seed(app_session)
    await clear_context(app_session)
    await set_tenant_context(app_session, user_id=ALICE, org_id=ORG_A)
    assert await visible(app_session, "SELECT id FROM organizations") == {str(ORG_A)}
    assert await visible(app_session, "SELECT org_id FROM memberships") == {str(ORG_A)}


async def test_user_sees_own_memberships_in_every_org(app_session: AsyncSession) -> None:
    """Needed for /me and the org switcher: Carol sees both of HER memberships, nothing else."""
    await seed(app_session)
    await clear_context(app_session)
    await set_tenant_context(app_session, user_id=CAROL)
    assert await visible(app_session, "SELECT id FROM organizations") == {str(ORG_A), str(ORG_B)}
    assert await visible(app_session, "SELECT user_id FROM memberships") == {str(CAROL)}


async def test_cannot_write_into_another_tenant(app_session: AsyncSession) -> None:
    await seed(app_session)
    await set_tenant_context(app_session, user_id=ALICE, org_id=ORG_A)
    with pytest.raises(DBAPIError, match="row-level security"):
        await app_session.execute(
            text("INSERT INTO memberships (org_id, user_id, role) VALUES (:o, :u, 'owner')"),
            {"o": ORG_B, "u": ALICE},
        )


async def test_cannot_modify_another_tenants_rows(app_session: AsyncSession) -> None:
    await seed(app_session)
    await set_tenant_context(app_session, user_id=ALICE, org_id=ORG_A)
    result = await app_session.execute(
        text("UPDATE memberships SET role = 'member' WHERE org_id = :o"), {"o": ORG_B}
    )
    assert result.rowcount == 0  # type: ignore[attr-defined]  # Bob's rows are invisible


async def test_every_tenant_table_has_rls(db_session: AsyncSession) -> None:
    """Guard for future migrations: any table with an org_id column must enable RLS."""
    unprotected = await db_session.scalars(
        text("""
            SELECT c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
              AND EXISTS (SELECT 1 FROM pg_attribute a
                          WHERE a.attrelid = c.oid AND a.attname = 'org_id' AND NOT a.attisdropped)
        """)
    )
    assert unprotected.all() == []


async def test_app_role_cannot_bypass_rls(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'agentflow_app'")
        )
    ).one()
    assert (row.rolsuper, row.rolbypassrls) == (False, False)
