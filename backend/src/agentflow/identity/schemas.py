"""API request/response shapes (Pydantic). Separate from the ORM models on purpose: the
database can gain columns (e.g. password_hash) without them leaking into API responses."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from agentflow.identity.models import Role
from agentflow.validation import non_blank

# Lowercased here to satisfy the users.email CHECK constraint.
Email = Annotated[EmailStr, AfterValidator(str.lower)]


class Industry(StrEnum):
    """The company's industry decides its data (record types to import) and the agents'
    tools: money-moving companies get ledger research, reversals, card freeze and dispute
    intelligence; transport companies get trips (schedules, seats, fares). Verticals are
    configuration (`capabilities`, `datasets.templates`), not code forks."""

    COMMERCIAL_BANK = "commercial_bank"
    MICROFINANCE_BANK = "microfinance_bank"
    DIGITAL_WALLET = "digital_wallet"  # wallets, payment service providers
    DIGITAL_LENDER = "digital_lender"
    TRANSPORT = "transport"  # intercity bus / coach operators


FINANCIAL = {
    Industry.COMMERCIAL_BANK,
    Industry.MICROFINANCE_BANK,
    Industry.DIGITAL_WALLET,
    Industry.DIGITAL_LENDER,
}


class Capabilities(BaseModel):
    """What an industry switches on."""

    money_tools: bool  # ledger, reversals, refunds, card freeze, disputes, approvals
    trips: bool  # schedules, seats and fares the agents answer from
    record_types: list[str]  # what the company can import (datasets.templates)


def capabilities_of(industry: Industry | None) -> Capabilities:
    if industry == Industry.TRANSPORT:
        return Capabilities(money_tools=False, trips=True, record_types=["customers", "trips"])
    # Financial, or no profile yet (companies from before the wizard were banks).
    return Capabilities(money_tools=True, trips=False, record_types=["customers", "transactions"])


def _valid_zone(zone: str) -> str:
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unknown time zone (use an IANA name like Asia/Karachi)") from None
    return zone


Channel = Literal["app_chat", "email", "whatsapp", "phone", "branch"]
Language = Literal["en", "ur", "roman_ur"]


class BusinessProfile(BaseModel):
    """What the company told us at signup. Sizes the ROI numbers and tailors the setup."""

    industry: Industry
    country: str = Field(default="PK", min_length=2, max_length=2)  # ISO 3166-1 alpha-2
    currency: str = Field(default="PKR", min_length=3, max_length=3)  # ISO 4217
    # IANA zone: imported times without a zone are read in it, and customers hear times in it
    # (QA: a 5 pm bus was announced as "17:00 UTC").
    timezone: Annotated[str, AfterValidator(_valid_zone)] = "Asia/Karachi"
    customers: Literal["under_10k", "10k_100k", "100k_1m", "over_1m"] = "10k_100k"
    monthly_queries: int = Field(default=5000, ge=0, le=10_000_000)  # support volume today
    channels: list[Channel] = ["app_chat"]
    languages: list[Language] = ["en", "roman_ur"]
    # For the ROI: how long a person spends on a routine query, and what an hour costs.
    minutes_per_query: int = Field(default=8, ge=1, le=240)
    agent_cost_per_hour: int = Field(default=1500, ge=0, le=1_000_000)  # in `currency`


class SignupRequest(BaseModel):
    email: Email
    # Length over complexity rules (NIST SP 800-63B). Max bounds hashing cost per request.
    password: str = Field(min_length=12, max_length=128)
    full_name: Annotated[str, non_blank(200)]
    org_name: Annotated[str, non_blank(200)]
    profile: BusinessProfile | None = None  # the signup wizard always sends it


class OrgProfileOut(BaseModel):
    org_id: uuid.UUID
    name: str
    profile: BusinessProfile | None
    capabilities: Capabilities


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

    org_name: Annotated[str, non_blank(200)]
    password: str = Field(min_length=1, max_length=128)
