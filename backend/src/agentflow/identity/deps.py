"""Request dependencies every module uses: settings, the current user, and the org context.

Tenant-scoped endpoints in any module declare one of:
    ctx: OrgContextDep      any member of the org named in the X-Org-Id header
    ctx: AdminContextDep    admins and owners only
and get back an OrgContext. Once it resolves, the transaction is bound to that org for
row-level security too.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select

from agentflow.config import Settings
from agentflow.db import SessionDep, set_tenant_context
from agentflow.identity.models import Membership, Organization, OrgStatus, Role, User, UserStatus
from agentflow.identity.security import decode_access_token
from agentflow.observability import org_id_var

# Reads "Authorization: Bearer <token>" and tells Swagger where to log in.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login")


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], session: SessionDep, settings: SettingsDep
) -> User:
    """Resolve the bearer token to an active user, or answer 401.

    The user is reloaded from the database on every request, so disabling an account takes
    effect immediately instead of when its token expires.
    """
    user_id = decode_access_token(token, settings)
    user = await session.get(User, user_id) if user_id else None  # users has no RLS
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    await set_tenant_context(session, user_id=user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass(frozen=True)
class OrgContext:
    org_id: uuid.UUID
    user: User
    role: Role


async def get_org_context(
    x_org_id: Annotated[uuid.UUID, Header(description="Organization this request acts in")],
    user: CurrentUser,
    session: SessionDep,
) -> OrgContext:
    """Check, against the database on every request, that the user belongs to the requested
    org; then bind the transaction to that org for row-level security.

    The org comes from a header, not from the token, so removing someone from an org or
    changing their role takes effect on their very next request.
    """
    row = (
        await session.execute(
            select(Membership.role, Organization.status)
            .join(Organization, Organization.id == Membership.org_id)
            .where(Membership.org_id == x_org_id, Membership.user_id == user.id)
        )
    ).one_or_none()
    # One answer for "no such org", "not a member" and "suspended": callers can't probe
    # which organization ids exist.
    if row is None or row.status != OrgStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this organization")
    await set_tenant_context(session, org_id=x_org_id)
    org_id_var.set(str(x_org_id))  # every later log line of this request carries the org
    return OrgContext(org_id=x_org_id, user=user, role=row.role)


OrgContextDep = Annotated[OrgContext, Depends(get_org_context)]


def require_role(minimum: Role) -> Any:  # FastAPI types Depends() as Any
    """Dependency: the org context, but only if the caller's role is at least `minimum`."""

    async def check(ctx: OrgContextDep) -> OrgContext:
        if not ctx.role.at_least(minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires the {minimum} role")
        return ctx

    return Depends(check)


AdminContextDep = Annotated[OrgContext, require_role(Role.ADMIN)]
