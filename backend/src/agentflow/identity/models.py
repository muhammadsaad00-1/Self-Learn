"""Identity tables: organizations (tenants), users, and memberships linking them with a role.

Users are global (one login can belong to several organizations); everything a tenant
owns hangs off `organizations.id` as `org_id`.
"""

import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base, TimestampMixin, enum_check, str_enum


class OrgStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class Role(StrEnum):
    OWNER = "owner"  # billing, delete org, manage admins
    ADMIN = "admin"  # manage members, tools, policies, knowledge
    MEMBER = "member"  # run workflows, act on approvals they're assigned


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (enum_check("status", OrgStatus),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63), unique=True)  # URL-safe handle
    plan: Mapped[str] = mapped_column(String(32), default="free")
    status: Mapped[OrgStatus] = mapped_column(str_enum(OrgStatus), default=OrgStatus.ACTIVE)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        # Emails are normalized to lowercase by the app; the DB guarantees it, so the plain
        # unique constraint below can never be bypassed by "Bob@x.com" vs "bob@x.com".
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        enum_check("status", UserStatus),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    # Null for users who sign in through an external identity provider (Cognito, Phase 7).
    password_hash: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[UserStatus] = mapped_column(str_enum(UserStatus), default=UserStatus.ACTIVE)


class Membership(TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (enum_check("role", Role),)

    # Composite primary key: a user has at most one membership (one role) per organization.
    # Its index (org_id, user_id) also serves "list members of org X".
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )  # separate index serves "list orgs of user Y"
    role: Mapped[Role] = mapped_column(str_enum(Role))
