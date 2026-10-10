"""Database plumbing shared by every domain module: ORM base, engine, per-request session."""

from collections.abc import AsyncGenerator
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import CheckConstraint, DateTime, Enum, MetaData, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names, so migrations can reference (and later drop) them by name
# instead of relying on whatever name Postgres would generate.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    # Set by the database (not Python) so all writers agree on one clock.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def str_enum[E: StrEnum](enum_cls: type[E]) -> Enum:
    """Map a StrEnum to a VARCHAR column storing its values (e.g. 'owner').

    Pair it with `enum_check()` in `__table_args__` so the database rejects other values.
    We avoid native Postgres ENUM types because adding a value needs ALTER TYPE, which is
    awkward inside migrations; a CHECK constraint is simply dropped and recreated.
    """
    return Enum(
        enum_cls,
        name=enum_cls.__name__.lower(),
        native_enum=False,
        # Not create_constraint=True: Alembic autogenerate renders that type-bound CHECK
        # twice, which breaks the migration. enum_check() declares it explicitly instead.
        create_constraint=False,
        length=32,
        values_callable=lambda cls: [m.value for m in cls],
        validate_strings=True,
    )


def enum_check(column: str, enum_cls: type[StrEnum]) -> CheckConstraint:
    """CHECK constraint allowing only `enum_cls` values in `column`."""
    allowed = ", ".join(f"'{m.value}'" for m in enum_cls)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"{column}_valid")


def create_engine(url: str) -> AsyncEngine:
    # pool_pre_ping: transparently replace connections the DB closed (restarts, idle timeouts).
    return create_async_engine(url, pool_pre_ping=True)


async def get_session(request: Request) -> AsyncGenerator[AsyncSession]:
    """One session and one transaction per request (unit of work).

    Commits when the endpoint returns, rolls back if it raises. Domain writes and their
    audit events (Phase 1d) therefore commit or fail together.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session, session.begin():
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
