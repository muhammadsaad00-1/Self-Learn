"""Security rules tested without HTTP: tokens, settings, role management."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr, ValidationError

from agentflow.config import Settings
from agentflow.identity.models import Role
from agentflow.identity.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from agentflow.identity.service import RoleChangeForbiddenError, check_can_manage

SETTINGS = Settings(env="test")


def test_token_round_trip() -> None:
    user_id = uuid.uuid4()
    assert decode_access_token(create_access_token(user_id, SETTINGS), SETTINGS) == user_id


def test_token_signed_with_other_secret_rejected() -> None:
    other = Settings(env="test", jwt_secret=SecretStr("x" * 48))
    assert decode_access_token(create_access_token(uuid.uuid4(), other), SETTINGS) is None


def test_unsigned_alg_none_token_rejected() -> None:
    """Classic JWT attack: a token claiming alg=none carries no signature at all."""
    now = datetime.now(UTC)
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "iss": "agentflow", "iat": now, "exp": now + timedelta(hours=1)},
        key=None,
        algorithm="none",
    )
    assert decode_access_token(forged, SETTINGS) is None


def test_password_hash_verifies_only_the_right_password() -> None:
    stored = hash_password("correct horse battery staple")
    assert stored.startswith("$argon2id$")
    assert verify_password("correct horse battery staple", stored)[0] is True
    assert verify_password("wrong", stored)[0] is False
    assert verify_password("anything", None)[0] is False  # no password set (SSO users)


def test_default_jwt_secret_refused_outside_local() -> None:
    with pytest.raises(ValidationError, match="AGENTFLOW_JWT_SECRET"):
        Settings(env="prod")
    with pytest.raises(ValidationError, match="CNIC_HASH_SECRET"):  # each key must be set
        Settings(env="prod", jwt_secret=SecretStr("a-real-secret-from-secrets-manager"))
    Settings(
        env="prod",
        jwt_secret=SecretStr("a-real-secret-from-secrets-manager"),
        cnic_hash_secret=SecretStr("another-real-secret"),
        audit_chain_secret=SecretStr("a-third-real-secret"),
    )  # ok


@pytest.mark.parametrize(
    ("actor", "current", "new", "allowed"),
    [
        (Role.MEMBER, Role.MEMBER, Role.ADMIN, False),  # members manage nobody
        (Role.ADMIN, None, Role.MEMBER, True),  # admin adds a member
        (Role.ADMIN, Role.MEMBER, Role.ADMIN, True),  # admin promotes to admin
        (Role.ADMIN, Role.MEMBER, Role.OWNER, False),  # only owners grant owner
        (Role.ADMIN, Role.OWNER, None, False),  # only owners remove an owner
        (Role.OWNER, Role.ADMIN, Role.OWNER, True),
        (Role.OWNER, Role.OWNER, None, True),  # (last-owner rule is checked separately)
    ],
)
def test_role_management_rules(
    actor: Role, current: Role | None, new: Role | None, allowed: bool
) -> None:
    if allowed:
        check_can_manage(actor, current, new)
    else:
        with pytest.raises(RoleChangeForbiddenError):
            check_can_manage(actor, current, new)
