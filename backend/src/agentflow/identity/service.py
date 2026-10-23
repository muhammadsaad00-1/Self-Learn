"""Identity use cases: signup, authentication, membership management.

Plain functions taking a session. No HTTP concepts here (status codes belong to api.py), so
the same logic can later be called from workers, CLIs or tests.
"""

import asyncio
import re
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.audit import service as audit
from agentflow.db import set_tenant_context
from agentflow.identity.models import Membership, Organization, Role, User, UserStatus
from agentflow.identity.schemas import (
    BusinessProfile,
    MemberOut,
    MembershipOut,
    OrgProfileOut,
    SignupRequest,
)
from agentflow.identity.security import hash_password, verify_password


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidPasswordError(Exception):
    pass


class MemberNotFoundError(Exception):
    pass


class AlreadyMemberError(Exception):
    pass


class RoleChangeForbiddenError(Exception):
    pass


class LastOwnerError(Exception):
    pass


def slugify(name: str) -> str:
    """'Daewoo Express (Lahore)' -> 'daewoo-express-lahore'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "org"


async def _insert_org_with_unique_slug(session: AsyncSession, org: Organization) -> None:
    """INSERT the org, adding a random suffix to its slug until it is unique.

    We can't SELECT first to find a free slug: row-level security hides other tenants'
    organizations, so every slug would look free. Instead try the INSERT inside a SAVEPOINT
    and let the UNIQUE constraint (which RLS doesn't affect) answer. A failed attempt rolls
    back only to the savepoint, not the whole signup transaction.
    """
    base = org.slug
    while True:
        try:
            async with session.begin_nested():  # SAVEPOINT ... RELEASE / ROLLBACK TO
                session.add(org)
            return
        except IntegrityError as e:
            if "uq_organizations_slug" not in str(e.orig):
                raise
            org.slug = f"{base}-{secrets.token_hex(3)}"


async def _create_org_with_owner(
    session: AsyncSession,
    user_id: uuid.UUID,
    org_name: str,
    *,
    via: str,
    profile: BusinessProfile | None = None,
) -> Organization:
    """A new organization (tenant) with `user_id` as its OWNER, plus the audit event.

    Shared by signup and "Add company": both end in exactly the same state, so the rules for
    what a new tenant looks like live in one place.
    """
    org = Organization(
        id=uuid.uuid4(),
        name=org_name,
        slug=slugify(org_name),
        profile=profile.model_dump(mode="json") if profile else None,
    )
    # Act inside the tenant being created, so the RLS policies' WITH CHECK accepts its rows.
    await set_tenant_context(session, user_id=user_id, org_id=org.id)
    await _insert_org_with_unique_slug(session, org)
    session.add(Membership(org_id=org.id, user_id=user_id, role=Role.OWNER))
    await audit.record(
        session,
        org_id=org.id,
        actor_user_id=user_id,
        action="org.created",
        target_type="organization",
        target_id=org.id,
        details={"via": via} | ({"industry": profile.industry} if profile else {}),
    )
    return org


async def signup(session: AsyncSession, data: SignupRequest) -> tuple[User, Organization]:
    """Create a user, their organization, and an OWNER membership, all in one transaction."""
    if await session.scalar(select(exists().where(User.email == data.email))):
        raise EmailAlreadyRegisteredError
    # Argon2 takes ~50 ms of CPU on purpose. Run it in a worker thread: calling it directly
    # in async code would freeze the event loop, and every other request with it.
    password_hash = await asyncio.to_thread(hash_password, data.password)
    user = User(
        id=uuid.uuid4(), email=data.email, full_name=data.full_name, password_hash=password_hash
    )
    session.add(user)
    await session.flush()  # the user row must exist before the membership points at it
    org = await _create_org_with_owner(
        session, user.id, data.org_name, via="signup", profile=data.profile
    )
    # ponytail: two simultaneous signups with one email both pass the check above; the
    # UNIQUE constraint still blocks the second (500, nothing saved). Map to 409 if ever seen.
    return user, org


async def add_company(
    session: AsyncSession, user: User, password: str, org_name: str
) -> MembershipOut:
    """Create another organization owned by an existing user (the "Add company" flow).

    Same login, new tenant: the org gets its own id, members, audit log and workflows, and
    nothing is copied from the user's other companies. The password is checked again because
    creating a company is a significant action (protects an unlocked, logged-in laptop).
    """
    valid, _ = await asyncio.to_thread(verify_password, password, user.password_hash)
    if not valid:
        raise InvalidPasswordError
    org = await _create_org_with_owner(session, user.id, org_name, via="add_company")
    return MembershipOut(org_id=org.id, org_name=org.name, org_slug=org.slug, role=Role.OWNER)


async def authenticate(session: AsyncSession, email: str, password: str) -> User | None:
    """The user if the credentials are valid and the account is active, else None.

    Every failure looks the same to the caller, so responses never reveal whether an email
    is registered.
    """
    user = await session.scalar(select(User).where(User.email == email.lower()))
    valid, new_hash = await asyncio.to_thread(
        verify_password, password, user.password_hash if user else None
    )
    if user is None or not valid or user.status != UserStatus.ACTIVE:
        return None
    if new_hash:  # hashing parameters were upgraded since this password was stored
        user.password_hash = new_hash
    return user


async def memberships_of(session: AsyncSession, user: User) -> list[MembershipOut]:
    rows = await session.execute(
        select(Membership.role, Organization.id, Organization.name, Organization.slug)
        .join(Organization, Organization.id == Membership.org_id)
        .where(Membership.user_id == user.id)
        .order_by(Organization.name)
    )
    return [
        MembershipOut(role=role, org_id=org_id, org_name=name, org_slug=slug)
        for role, org_id, name, slug in rows
    ]


# --- Membership management -------------------------------------------------------------
# Every function filters by org_id explicitly. Row-level security would also hide other
# orgs' rows, but it is the safety net, not the mechanism: queries must be correct without it.


def check_can_manage(actor: Role, current: Role | None, new: Role | None) -> None:
    """Admins and owners manage members; anything involving the OWNER role (granting it,
    or changing or removing an owner) needs an owner."""
    if not actor.at_least(Role.ADMIN):
        raise RoleChangeForbiddenError
    if Role.OWNER in (current, new) and actor != Role.OWNER:
        raise RoleChangeForbiddenError


async def _lock_org(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Serialize membership changes within one org until this transaction ends.

    Without it, two owners demoting each other at the same moment would each see "another
    owner exists" and both succeed, leaving the org with no owner.
    """
    await session.execute(
        select(Organization.id).where(Organization.id == org_id).with_for_update()
    )


async def _ensure_another_owner(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    owners = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.org_id == org_id,
            Membership.role == Role.OWNER,
            Membership.user_id != user_id,
        )
    )
    if not owners:
        raise LastOwnerError


async def _get_membership(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> Membership:
    membership = await session.get(Membership, (org_id, user_id))
    if membership is None:
        raise MemberNotFoundError
    return membership


async def _member_out(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> MemberOut:
    return next(m for m in await list_members(session, org_id) if m.user_id == user_id)


async def list_members(session: AsyncSession, org_id: uuid.UUID) -> list[MemberOut]:
    rows = await session.execute(
        select(User.id, User.email, User.full_name, Membership.role, Membership.created_at)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org_id)
        .order_by(Membership.created_at)
    )
    return [
        MemberOut(user_id=uid, email=email, full_name=name, role=role, joined_at=joined)
        for uid, email, name, role, joined in rows
    ]


@dataclass(frozen=True)
class Actor:
    """Who is making a membership change, and with which role in the org."""

    user_id: uuid.UUID
    role: Role


async def add_member(
    session: AsyncSession, org_id: uuid.UUID, actor: Actor, email: str, role: Role
) -> MemberOut:
    check_can_manage(actor.role, None, role)
    user_id = await session.scalar(select(User.id).where(User.email == email))
    if user_id is None:
        raise MemberNotFoundError
    if await session.get(Membership, (org_id, user_id)):
        raise AlreadyMemberError
    session.add(Membership(org_id=org_id, user_id=user_id, role=role))
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=actor.user_id,
        action="member.added",
        target_type="user",
        target_id=user_id,
        details={"role": role},
    )
    await session.flush()
    return await _member_out(session, org_id, user_id)


async def change_role(
    session: AsyncSession, org_id: uuid.UUID, actor: Actor, user_id: uuid.UUID, role: Role
) -> MemberOut:
    await _lock_org(session, org_id)
    membership = await _get_membership(session, org_id, user_id)
    check_can_manage(actor.role, membership.role, role)
    if membership.role == Role.OWNER and role != Role.OWNER:
        await _ensure_another_owner(session, org_id, user_id)
    previous, membership.role = membership.role, role
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=actor.user_id,
        action="member.role_changed",
        target_type="user",
        target_id=user_id,
        details={"from": previous, "to": role},
    )
    await session.flush()
    return await _member_out(session, org_id, user_id)


async def remove_member(
    session: AsyncSession, org_id: uuid.UUID, actor: Actor, user_id: uuid.UUID
) -> None:
    await _lock_org(session, org_id)
    membership = await _get_membership(session, org_id, user_id)
    check_can_manage(actor.role, membership.role, None)
    if membership.role == Role.OWNER:
        await _ensure_another_owner(session, org_id, user_id)
    await session.delete(membership)
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=actor.user_id,
        action="member.removed",
        target_type="user",
        target_id=user_id,
        details={"role": membership.role},
    )


class OrgNotFoundError(Exception):
    pass


async def get_profile(session: AsyncSession, org_id: uuid.UUID) -> OrgProfileOut:
    org = await session.get(Organization, org_id)
    if org is None:
        raise OrgNotFoundError
    return OrgProfileOut(
        org_id=org.id,
        name=org.name,
        profile=BusinessProfile.model_validate(org.profile) if org.profile else None,
    )


async def set_profile(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, profile: BusinessProfile
) -> OrgProfileOut:
    org = await session.get(Organization, org_id)
    assert org is not None  # noqa: S101
    org.profile = profile.model_dump(mode="json")
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=user_id,
        action="org.profile_updated",
        target_type="organization",
        target_id=org_id,
        details={"industry": profile.industry},
    )
    return await get_profile(session, org_id)
