"""HTTP endpoints for authentication, plus the `CurrentUser` dependency other modules use."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from redis.asyncio import Redis

from agentflow.config import Settings
from agentflow.db import SessionDep
from agentflow.identity import service
from agentflow.identity.models import User, UserStatus
from agentflow.identity.schemas import (
    MeResponse,
    SignupRequest,
    SignupResponse,
    TokenResponse,
    UserOut,
)
from agentflow.identity.security import create_access_token, decode_access_token

router = APIRouter(prefix="/v1", tags=["auth"])

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
    user = await session.get(User, user_id) if user_id else None
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


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
