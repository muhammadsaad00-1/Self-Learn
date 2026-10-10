"""Shared test fixtures.

Integration tests run against a real Postgres (docker compose locally, a service container in
CI) in a separate `<db>_test` database, so your local dev data is never touched.
"""

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from agentflow.config import Settings

BACKEND_DIR = Path(__file__).parents[1]


def alembic_config(database_url: str) -> Config:
    cfg = Config(toml_file=str(BACKEND_DIR / "pyproject.toml"))
    cfg.attributes["database_url"] = database_url  # read by migrations/env.py
    return cfg


@pytest.fixture(scope="session")
def database_url() -> str:
    """URL of a fresh test database: dropped, recreated and migrated once per test run.

    Built by the real migrations (not `metadata.create_all`), so every run also proves the
    migrations produce a working schema.
    """
    base = make_url(str(Settings().database_url))
    test = base.set(database=f"{base.database}_test")

    async def recreate() -> None:
        # CREATE/DROP DATABASE can't run inside a transaction, hence AUTOCOMMIT.
        engine = create_async_engine(base, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{test.database}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{test.database}"'))
        await engine.dispose()

    asyncio.run(recreate())
    url = test.render_as_string(hide_password=False)
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
async def db_session(database_url: str) -> AsyncGenerator[AsyncSession]:
    """A session whose work is rolled back after the test, so tests never see each other's rows.

    The outer transaction is never committed. Code under test that calls commit() only
    releases a SAVEPOINT inside it (join_transaction_mode="create_savepoint").
    """
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        yield session
        await session.close()
        await outer.rollback()
    await engine.dispose()
