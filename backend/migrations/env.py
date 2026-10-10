"""Alembic migration environment.

The database URL comes from `config.attributes["database_url"]` when set in code (tests), otherwise
from AgentFlow settings, i.e. the same AGENTFLOW_DATABASE_URL the app uses.
"""

import asyncio
import logging

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from agentflow.config import get_settings
from agentflow.models import Base

config = context.config
target_metadata = Base.metadata

if not logging.getLogger().handlers:  # CLI run: show "Running upgrade ..." lines
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")


def database_url() -> str:
    url: str | None = config.attributes.get("database_url")
    return url or str(get_settings().database_url)


def run_migrations_offline() -> None:
    """`alembic upgrade --sql`: render SQL without connecting (for DBA review)."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
