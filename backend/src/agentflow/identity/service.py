"""Identity use cases: signup, authentication, membership lookup.

Plain functions taking a session. No HTTP concepts here (status codes belong to api.py), so
the same logic can later be called from workers, CLIs or tests.
"""

import asyncio
import re
import secrets

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.identity.models import Membership, Organization, Role, User, UserStatus
from agentflow.identity.schemas import MembershipOut, SignupRequest
from agentflow.identity.security import hash_password, verify_password


class EmailAlreadyRegisteredError(Exception):
    pass


def slugify(name: str) -> str:
    """'Daewoo Express (Lahore)' -> 'daewoo-express-lahore'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "org"


async def _unique_slug(session: AsyncSession, name: str) -> str:
    base = slug = slugify(name)
    while await session.scalar(select(exists().where(Organization.slug == slug))):
        slug = f"{base}-{secrets.token_hex(3)}"
    return slug


async def signup(session: AsyncSession, data: SignupRequest) -> tuple[User, Organization]:
    """Create a user, their organization, and an OWNER membership, all in one transaction."""
    if await session.scalar(select(exists().where(User.email == data.email))):
        raise EmailAlreadyRegisteredError
    # Argon2 takes ~50 ms of CPU on purpose. Run it in a worker thread: calling it directly
    # in async code would freeze the event loop, and every other request with it.
    password_hash = await asyncio.to_thread(hash_password, data.password)
    user = User(email=data.email, full_name=data.full_name, password_hash=password_hash)
    org = Organization(name=data.org_name, slug=await _unique_slug(session, data.org_name))
    session.add_all([user, org])
    await session.flush()  # INSERT now so the rows exist before the membership references them
    session.add(Membership(org_id=org.id, user_id=user.id, role=Role.OWNER))
    # ponytail: two simultaneous signups with one email pass the check above; the UNIQUE
    # constraint still blocks the second (500, nothing saved). Map IntegrityError to 409 if seen.
    return user, org


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
