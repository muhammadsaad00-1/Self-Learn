"""Application settings, read from environment variables (12-factor).

Locally they come from `.env` (loaded by the Makefile / Docker Compose);
on AWS they come from the ECS task definition and Secrets Manager.
"""

from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTFLOW_", extra="ignore")

    env: Literal["local", "test", "dev", "staging", "prod"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Defaults match docker-compose.yml so `make run` works with zero setup.
    # Non-local environments must set these explicitly.
    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://agentflow:agentflow@localhost:5432/agentflow"
    )
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")


@lru_cache
def get_settings() -> Settings:
    return Settings()
