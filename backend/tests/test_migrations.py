"""Migration safety nets: the schema matches the models, and migrations reverse cleanly."""

from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from agentflow.models import Base
from tests.conftest import alembic_config


async def test_models_match_migrated_schema(database_url: str) -> None:
    """Fails if someone changes a model without writing the matching migration."""

    def diff(conn: Connection) -> list[Any]:
        changes: list[Any] = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        return changes

    engine = create_async_engine(database_url)
    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []
    await engine.dispose()


def test_downgrade_to_base_and_back(database_url: str) -> None:
    """Every migration's downgrade() works, so a bad deploy can be rolled back."""
    cfg = alembic_config(database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
