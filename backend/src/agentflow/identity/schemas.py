"""API request/response shapes (Pydantic). Separate from the ORM models on purpose: the
database can gain columns (e.g. password_hash) without them leaking into API responses."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from agentflow.identity.models import Role

# Lowercased here to satisfy the users.email CHECK constraint.
Email = Annotated[EmailStr, AfterValidator(str.lower)]


class SignupRequest(BaseModel):
    email: Email
    # Length over complexity rules (NIST SP 800-63B). Max bounds hashing cost per request.
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    org_name: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    # Field names follow the OAuth2 spec so Swagger's "Authorize" button understands them.
    access_token: str
    token_type: str = "bearer"  # noqa: S105 (OAuth2 token type name, not a secret)
    expires_in: int  # seconds


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # build from ORM objects

    id: uuid.UUID
    email: str
    full_name: str


class MembershipOut(BaseModel):
    org_id: uuid.UUID
    org_name: str
    org_slug: str
    role: Role


class MeResponse(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]


class SignupResponse(TokenResponse):
    user: UserOut
    org_id: uuid.UUID


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    joined_at: datetime


class AddMemberRequest(BaseModel):
    """Add an existing account to the org. Email invitations for people without an account
    arrive with the email tool (Phase 5)."""

    email: Email
    role: Role = Role.MEMBER


class ChangeRoleRequest(BaseModel):
    role: Role


class AddCompanyRequest(BaseModel):
    """Create another company for the logged-in user. Name, email and password stay the same;
    the password is asked again to confirm it's really them."""

    org_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=128)
