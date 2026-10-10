"""The database itself enforces identity invariants, even if application code has a bug."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.identity.models import Membership, Organization, OrgStatus, Role, User


async def make_org_and_user(session: AsyncSession) -> tuple[Organization, User]:
    org = Organization(name="Daewoo Express", slug="daewoo-express")
    user = User(email="owner@daewoo.example", full_name="Ayesha Khan", password_hash="x")
    session.add_all([org, user])
    await session.flush()
    return org, user


async def test_defaults_are_applied(db_session: AsyncSession) -> None:
    org, _ = await make_org_and_user(db_session)
    await db_session.refresh(org)
    assert org.status == OrgStatus.ACTIVE
    assert org.plan == "free"
    assert org.created_at is not None


async def test_email_must_be_lowercase(db_session: AsyncSession) -> None:
    db_session.add(User(email="Mixed@Case.example", full_name="X", password_hash="x"))
    with pytest.raises(IntegrityError, match="ck_users_email_lowercase"):
        await db_session.flush()


async def test_one_membership_per_user_per_org(db_session: AsyncSession) -> None:
    org, user = await make_org_and_user(db_session)
    db_session.add(Membership(org_id=org.id, user_id=user.id, role=Role.OWNER))
    await db_session.flush()
    db_session.add(Membership(org_id=org.id, user_id=user.id, role=Role.MEMBER))
    with pytest.raises(IntegrityError, match="pk_memberships"):
        await db_session.flush()


async def test_unknown_role_rejected_by_database(db_session: AsyncSession) -> None:
    """Raw SQL bypasses the ORM's enum validation, so this exercises the CHECK constraint."""
    org, user = await make_org_and_user(db_session)
    with pytest.raises(IntegrityError, match="ck_memberships_role_valid"):
        await db_session.execute(
            text("INSERT INTO memberships (org_id, user_id, role) VALUES (:o, :u, 'superuser')"),
            {"o": org.id, "u": user.id},
        )


async def test_deleting_org_removes_its_memberships(db_session: AsyncSession) -> None:
    org, user = await make_org_and_user(db_session)
    db_session.add(Membership(org_id=org.id, user_id=user.id, role=Role.OWNER))
    await db_session.flush()
    await db_session.execute(text("DELETE FROM organizations WHERE id = :o"), {"o": org.id})
    remaining = await db_session.scalars(select(Membership).where(Membership.user_id == user.id))
    assert remaining.all() == []
