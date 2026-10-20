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
    # Model routes, tried in order: the next model is used when the previous is rate-limited
    # or down. All enforce our JSON schema strictly on Groq (others only try to) and have
    # separate rate-limit budgets. They share one provider, so surviving a Groq outage needs a
    # second provider (Bedrock, Phase 7).
    # fast: classification and query planning. strong: grounded answers and verification,
    # where a wrong judgement costs more than a few hundred milliseconds.
    llm_fast_models: list[str] = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
    llm_strong_models: list[str] = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    llm_timeout_seconds: float = 30

    # --- Knowledge / RAG (ADR 0011) ---
    embedding_model: str = "BAAI/bge-small-en-v1.5"  # 384-d, matches knowledge_chunks
    reranker_model: str = "BAAI/bge-reranker-v2-m3-int8"  # multilingual cross-encoder
    model_cache_dir: str | None = None  # where model files are cached (a volume in docker)
    retrieval_candidates: int = 30  # per retriever (vector, keyword) per query
    # Fused chunks the cross-encoder scores. ~280 ms each on CPU (bge-m3 int8): 10 keeps
    # R@5 at 95% on the eval set in ~2.8 s; 20 gains R@1 but doubles the time.
    rerank_candidates: int = 10
    retrieval_top_k: int = 5  # sources handed to the answer agent
    rrf_vector_weight: float = 1.0
    rrf_keyword_weight: float = 0.6  # keyword is noisier for natural sentences (demo finding)
    # Below this best rerank score the system refuses to answer without calling the LLM.
    # Reranker logits are model-specific: calibrated with `make eval-rag`. On raw questions
    # answerable and out-of-scope scores overlap, so this only blocks clearly irrelevant
    # results; borderline cases are left to the answer agent (abstain) and the verifier.
    min_rerank_score: float = -8.0
    prompt_guard_model: str = "meta-llama/llama-prompt-guard-2-86m"  # ingestion screening
    max_upload_bytes: int = 10 * 1024 * 1024
    knowledge_asks_per_minute: int = 20  # per user; each question is 3-5 model calls
    document_max_attempts: int = 3
    document_lease_seconds: int = 300

    # --- Object storage (S3 API: MinIO locally, AWS S3 in the cloud) ---
    s3_endpoint_url: str | None = "http://localhost:9000"  # None = real AWS S3
    s3_bucket: str = "agentflow-documents"
    s3_access_key_id: str | None = "agentflow"  # local MinIO only; on AWS use the IAM role
    s3_secret_access_key: SecretStr | None = SecretStr("agentflow-local-secret")
    s3_region: str = "us-east-1"

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
