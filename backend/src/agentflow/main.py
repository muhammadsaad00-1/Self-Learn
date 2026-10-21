"""FastAPI application factory.

Run with: uvicorn agentflow.main:create_app --factory
A factory (instead of a module-level `app`) lets tests build apps with
their own settings, and avoids side effects at import time.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from agentflow import __version__
from agentflow.approvals import api as approvals_api
from agentflow.audit import api as audit_api
from agentflow.config import Settings, get_settings
from agentflow.db import create_engine
from agentflow.identity import api as identity_api
from agentflow.knowledge import api as knowledge_api
from agentflow.knowledge.runtime import KnowledgeRuntime
from agentflow.ledger import api as ledger_api
from agentflow.llm import StructuredLLM
from agentflow.observability import configure_logging, request_context_middleware
from agentflow.workflow import api as workflow_api


def create_app(
    settings: Settings | None = None,
    *,
    llm: StructuredLLM | None = None,
    knowledge: KnowledgeRuntime | None = None,
) -> FastAPI:
    """`llm` and `knowledge` are injectable for tests; by default they're built on first
    use (see knowledge.api), so starting the API never loads models or needs a Groq key."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # Pools are created once per process and shared by all requests.
        engine = create_engine(str(settings.database_url))
        app.state.engine = engine
        app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        app.state.redis = Redis.from_url(str(settings.redis_url))
        yield
        await app.state.redis.aclose()
        await engine.dispose()

    app = FastAPI(
        title="AgentFlow",
        version=__version__,
        lifespan=lifespan,
        # Interactive docs only outside production.
        docs_url=None if settings.env == "prod" else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.llm = llm
    app.state.knowledge = knowledge
    app.middleware("http")(request_context_middleware)
    app.include_router(identity_api.router)
    app.include_router(identity_api.members_router)
    app.include_router(identity_api.orgs_router)
    app.include_router(audit_api.router)
    app.include_router(workflow_api.router)
    app.include_router(knowledge_api.router)
    app.include_router(ledger_api.router)
    app.include_router(approvals_api.router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Liveness: the process is up. Never checks dependencies, so a DB outage
        doesn't make the orchestrator kill healthy containers in a restart loop."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"])
    async def ready(request: Request) -> JSONResponse:
        """Readiness: can this instance serve traffic? The load balancer stops routing to it
        (503) while Postgres or Redis is unreachable."""
        checks: dict[str, str] = {}
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "unavailable"
        try:
            await request.app.state.redis.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "unavailable"
        healthy = all(v == "ok" for v in checks.values())
        return JSONResponse(
            {"status": "ok" if healthy else "unavailable", "checks": checks},
            status_code=200 if healthy else 503,
        )

    return app
