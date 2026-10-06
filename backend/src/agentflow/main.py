"""FastAPI application factory.

Run with: uvicorn agentflow.main:create_app --factory
A factory (instead of a module-level `app`) lets tests build apps with
their own settings, and avoids side effects at import time.
"""

from fastapi import FastAPI

from agentflow import __version__
from agentflow.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="AgentFlow",
        version=__version__,
        # Interactive docs only outside production.
        docs_url=None if settings.env == "prod" else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Liveness probe: the process is up. Readiness (DB/Redis) lands in Phase 1."""
        return {"status": "ok", "version": __version__}

    return app
