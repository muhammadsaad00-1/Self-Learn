"""HTTP endpoints for authentication and organization membership."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from redis.asyncio import Redis

from agentflow.config import Settings
from agentflow.db import SessionDep
from agentflow.identity import service
from agentflow.identity.deps import (
    AdminContextDep,
    CurrentUser,
    OrgContext,
    OrgContextDep,
    SettingsDep,
)
from agentflow.identity.models import User
from agentflow.identity.schemas import (
    AddMemberRequest,
    ChangeRoleRequest,
    MemberOut,
    MeResponse,
    SignupRequest,
    SignupResponse,
    TokenResponse,
    UserOut,
)
from agentflow.identity.security import create_access_token

router = APIRouter(prefix="/v1", tags=["auth"])
members_router = APIRouter(prefix="/v1/members", tags=["members"])


def _token_response(user: User, settings: Settings) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id, settings),
        expires_in=settings.access_token_ttl_minutes * 60,
    )


async def _enforce_login_rate_limit(redis: Redis, email: str, settings: Settings) -> None:
    """At most `login_max_attempts` per email per window (fixed window, counted in Redis).

    Checked before the password, so a password-guessing attacker also can't make us burn
    Argon2 CPU. Redis is shared by every API instance, so the limit holds across replicas.
    """
    key = f"login-attempts:{email}"
    # SET NX creates the counter with its expiry in one atomic step; INCR keeps that expiry.
    await redis.set(key, 0, ex=settings.login_window_seconds, nx=True)
    if await redis.incr(key) > settings.login_max_attempts:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts, try again later",
            headers={"Retry-After": str(await redis.ttl(key))},
        )


@router.post("/auth/signup", status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, session: SessionDep, settings: SettingsDep) -> SignupResponse:
    """Create an account and its first organization; returns a token so the user is logged in."""
    try:
        user, org = await service.signup(session, body)
    except service.EmailAlreadyRegisteredError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None
    token = _token_response(user, settings)
    return SignupResponse(**token.model_dump(), user=UserOut.model_validate(user), org_id=org.id)


@router.post("/auth/login")
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    """OAuth2 password flow: form fields `username` (the email) and `password`."""
    await _enforce_login_rate_limit(request.app.state.redis, form.username.lower(), settings)
    user = await service.authenticate(session, form.username, form.password)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _token_response(user, settings)


@router.get("/me")
async def me(user: CurrentUser, session: SessionDep) -> MeResponse:
    """The logged-in user and the organizations they belong to (for the org switcher)."""
    memberships = await service.memberships_of(session, user)
    return MeResponse(user=UserOut.model_validate(user), memberships=memberships)


# --- Organization members (X-Org-Id header selects the org) ----------------------------

_MEMBER_ERRORS: dict[type[Exception], tuple[int, str]] = {
    service.MemberNotFoundError: (status.HTTP_404_NOT_FOUND, "No such member or account"),
    service.AlreadyMemberError: (status.HTTP_409_CONFLICT, "Already a member"),
    service.RoleChangeForbiddenError: (
        status.HTTP_403_FORBIDDEN,
        "Only owners can grant, change or remove the owner role",
    ),
    service.LastOwnerError: (status.HTTP_409_CONFLICT, "An organization must keep an owner"),
}


def _actor(ctx: OrgContext) -> service.Actor:
    return service.Actor(user_id=ctx.user.id, role=ctx.role)


def _member_http_error(e: Exception) -> HTTPException:
    code, detail = _MEMBER_ERRORS[type(e)]
    return HTTPException(code, detail)


@members_router.get("")
async def list_members(ctx: OrgContextDep, session: SessionDep) -> list[MemberOut]:
    """Everyone in the organization, with their role. Any member can see this."""
    return await service.list_members(session, ctx.org_id)


@members_router.post("", status_code=status.HTTP_201_CREATED)
async def add_member(
    body: AddMemberRequest, ctx: AdminContextDep, session: SessionDep
) -> MemberOut:
    try:
        return await service.add_member(session, ctx.org_id, _actor(ctx), body.email, body.role)
    except tuple(_MEMBER_ERRORS) as e:
        raise _member_http_error(e) from None


@members_router.patch("/{user_id}")
async def change_member_role(
    user_id: uuid.UUID, body: ChangeRoleRequest, ctx: AdminContextDep, session: SessionDep
) -> MemberOut:
    try:
        return await service.change_role(session, ctx.org_id, _actor(ctx), user_id, body.role)
    except tuple(_MEMBER_ERRORS) as e:
        raise _member_http_error(e) from None


@members_router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: uuid.UUID, ctx: AdminContextDep, session: SessionDep) -> Response:
    """Access ends on the member's next request (membership is checked every time)."""
    try:
        await service.remove_member(session, ctx.org_id, _actor(ctx), user_id)
    except tuple(_MEMBER_ERRORS) as e:
        raise _member_http_error(e) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
