"""Password hashing and access tokens (JWT).

Passwords: we store only an Argon2 hash. Argon2 is deliberately slow and memory-hungry,
so a leaked users table can't be brute-forced at GPU speed.

Tokens: a JWT is a signed, base64-encoded JSON payload. The server signs it with
`jwt_secret`; later it verifies the signature instead of looking anything up. The payload is
readable by anyone (not encrypted), so it holds only the user id, never secrets.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from agentflow.config import Settings

_hasher = PasswordHash.recommended()  # Argon2id with current recommended parameters
_ISSUER = "agentflow"
_ALGORITHM = "HS256"  # HMAC-SHA256: one shared secret both signs and verifies

# Hash of a random value, used to spend the same time on unknown emails as on real ones.
# Without it, "no such user" answers measurably faster than "wrong password", which lets an
# attacker discover which emails are registered.
_DUMMY_HASH = _hasher.hash(uuid.uuid4().hex)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> tuple[bool, str | None]:
    """Return (is_valid, new_hash). new_hash is set when the stored hash uses outdated
    parameters and should be replaced; the plaintext is only available at login."""
    if password_hash is None:
        _hasher.verify(password, _DUMMY_HASH)  # equalize timing, result ignored
        return False, None
    return _hasher.verify_and_update(password, password_hash)


def create_access_token(user_id: uuid.UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),  # subject: who this token is about
        "iss": _ISSUER,  # issuer: who created it
        "iat": now,  # issued at
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),  # expiry
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=_ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> uuid.UUID | None:
    """User id from a valid token; None if the signature, expiry or issuer is wrong."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[_ALGORITHM],  # pinned: never let the token choose its own algorithm
            issuer=_ISSUER,
            options={"require": ["sub", "exp", "iat", "iss"]},
        )
        return uuid.UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError):
        return None
