"""Application settings, read from environment variables (12-factor).

Locally they come from `.env` (loaded by the Makefile / Docker Compose);
on AWS they come from the ECS task definition and Secrets Manager.
"""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Only valid for local/test; Settings refuses to start elsewhere while it's still in use.
_DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-0123456789"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTFLOW_", extra="ignore")

    env: Literal["local", "test", "dev", "staging", "prod"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Defaults match docker-compose.yml so `make run` works with zero setup.
    # Non-local environments must set these explicitly.
    # The app connects as `agentflow_app` (no superuser, no BYPASSRLS) so row-level security
    # applies; migrations connect as the schema owner. See infra/postgres/app-role.sql.
    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://agentflow_app:agentflow_app@localhost:5432/agentflow"
    )
    migration_database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://agentflow:agentflow@localhost:5432/agentflow"
    )
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")

    # Key that signs login tokens. Anyone holding it can mint a token for any user, so on AWS
    # it comes from Secrets Manager. SecretStr keeps it out of logs and reprs.
    jwt_secret: SecretStr = SecretStr(_DEV_JWT_SECRET)
    access_token_ttl_minutes: int = 30
    login_max_attempts: int = 10  # per email, per window
    login_window_seconds: int = 15 * 60

    # --- LLM (ADR 0002: Groq first, behind our own router) ---
    # Read from GROQ_API_KEY (the provider's conventional name) as well as the prefixed form.
    groq_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("AGENTFLOW_GROQ_API_KEY", "GROQ_API_KEY")
    )
    # Models tried in order for classification: the next one is used when the previous is
    # rate-limited or down. Both enforce our JSON schema strictly on Groq (others only try to)
    # and have separate rate-limit budgets. They share one provider, so surviving a Groq
    # outage needs a second provider (Bedrock, Phase 7).
    llm_classification_models: list[str] = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
    llm_timeout_seconds: float = 30

    # --- Workflow worker ---
    workflow_max_attempts: int = 3  # then the workflow is failed (dead-lettered)
    workflow_lease_seconds: int = 120  # a crashed worker's job is reclaimed after this
    workflow_timeout_seconds: float = 90  # wall-clock budget for one graph run

    @model_validator(mode="after")
    def _no_dev_secrets_outside_local(self) -> "Settings":
        if self.env not in ("local", "test") and (
            self.jwt_secret.get_secret_value() == _DEV_JWT_SECRET
        ):
            raise ValueError("AGENTFLOW_JWT_SECRET must be set outside local/test")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
