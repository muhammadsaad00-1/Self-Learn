"""Shared test fixtures.

Integration tests run against a real Postgres (docker compose locally, a service container in
CI) in a separate `<db>_test` database, so your local dev data is never touched.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from agentflow.config import Settings
from agentflow.knowledge.runtime import KnowledgeRuntime
from agentflow.main import create_app
from agentflow.models import Base
from tests.fakes import fake_runtime

BACKEND_DIR = Path(__file__).parents[1]
APP_ROLE_SQL = BACKEND_DIR.parent / "infra" / "postgres" / "app-role.sql"


def _test_db(dsn: object) -> str:
    """Same server and credentials, but the `<db>_test` database.

    Takes the settings value, never a SQLAlchemy URL: str(URL) masks the password as ***.
    """
    parsed = make_url(str(dsn))
    return parsed.set(database=f"{parsed.database}_test").render_as_string(hide_password=False)


def alembic_config(database_url: str) -> Config:
    cfg = Config(toml_file=str(BACKEND_DIR / "pyproject.toml"))
    cfg.attributes["database_url"] = database_url  # read by migrations/env.py
    return cfg


@pytest.fixture(scope="session")
def database_url() -> str:
    """Owner (migration) URL of a fresh test database: dropped, recreated, migrated per run.

    Built by the real migrations (not `metadata.create_all`), so every run also proves the
    migrations produce a working schema. Connections with this URL bypass row-level security
    (it owns the tables), which suits setup and invariant tests; see `app_database_url`.
    """
    owner_dsn = str(Settings().migration_database_url)
    owner, test = make_url(owner_dsn), make_url(_test_db(owner_dsn))

    async def recreate() -> None:
        # CREATE/DROP DATABASE can't run inside a transaction, hence AUTOCOMMIT.
        engine = create_async_engine(owner, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.execute(text(APP_ROLE_SQL.read_text()))  # roles are server-wide
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{test.database}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{test.database}"'))
        await engine.dispose()

    asyncio.run(recreate())
    url = test.render_as_string(hide_password=False)
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture(scope="session")
def app_database_url(database_url: str) -> str:
    """The test database as the app's low-privilege role: row-level security applies."""
    return _test_db(Settings().database_url)


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


@pytest.fixture
async def app_session(app_database_url: str) -> AsyncGenerator[AsyncSession]:
    """Like `db_session`, but connected as the app role, so row-level security applies."""
    engine = create_async_engine(app_database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        outer = await conn.begin()
        yield AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
        await outer.rollback()
    await engine.dispose()


@pytest.fixture
def settings(database_url: str, app_database_url: str) -> Iterator[Settings]:
    """Settings for tests that go through the real app, which really commits. Afterwards every
    table is emptied, so committed rows never leak into the next test."""
    # Redis: a database of their own (never the dev stack's db 0), emptied before every test,
    # so rate-limit and one-time-code keys can't leak between tests or from manual runs.
    redis_url = Settings().redis_url.unicode_string().rsplit("/", 1)[0] + "/15"

    async def flush() -> None:
        redis = Redis.from_url(redis_url)
        await redis.flushdb()
        await redis.aclose()

    asyncio.run(flush())
    yield Settings(
        env="test",
        database_url=app_database_url,
        migration_database_url=database_url,
        redis_url=redis_url,
    )

    async def truncate() -> None:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        engine = create_async_engine(database_url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
        await engine.dispose()

    asyncio.run(truncate())


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """HTTP client for the real app (lifespan runs, real commits to the test DB).

    Tables are cleaned by `settings`, and Redis (db 15) is emptied before each test.
    """
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def run_sql(database_url: str) -> Callable[[str, dict[str, object]], None]:
    """Execute and commit raw SQL from a sync test, e.g. to set up state no endpoint exposes."""

    def run(sql: str, params: dict[str, object]) -> None:
        async def go() -> None:
            engine = create_async_engine(database_url, poolclass=NullPool)
            async with engine.begin() as conn:
                await conn.execute(text(sql), params)
            await engine.dispose()

        asyncio.run(go())

    return run


@pytest.fixture
def exec_sql(database_url: str) -> Callable[[str, dict[str, object]], Awaitable[None]]:
    """Async twin of `run_sql`, for async tests (asyncio.run can't nest in a running loop)."""

    async def run(sql: str, params: dict[str, object]) -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(text(sql), params)
        await engine.dispose()

    return run


@pytest.fixture
async def sessions(settings: Settings) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    """Session factory as the app role, exactly like the worker process uses."""
    engine = create_async_engine(str(settings.database_url))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def runtime() -> KnowledgeRuntime:
    """Fake models + in-memory storage for the knowledge module (see tests/fakes.py)."""
    return fake_runtime()


@pytest.fixture
def client_for(settings: Settings, runtime: KnowledgeRuntime) -> Callable[[Any], Any]:
    """Factory: a TestClient whose app uses the given (fake) LLM and the fake runtime."""

    @contextmanager
    def make(llm: Any) -> Iterator[TestClient]:
        with TestClient(create_app(settings, llm=llm, knowledge=runtime)) as client:
            yield client

    return make
