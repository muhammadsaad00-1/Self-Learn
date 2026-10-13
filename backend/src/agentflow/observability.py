"""Structured JSON logs with correlation ids.

Every log line carries the ids of what it belongs to (request, org, workflow), so one
customer request can be followed across the API, the worker and every AI call:

    {"ts": "...", "level": "INFO", "msg": "llm.call", "request_id": "...", "model": "...", ...}

The ids live in contextvars: per-asyncio-task variables, so concurrent requests never see
each other's ids. OpenTelemetry tracing (spans, exporters) builds on these ids later.
"""

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
org_id_var: ContextVar[str | None] = ContextVar("org_id", default=None)
workflow_id_var: ContextVar[str | None] = ContextVar("workflow_id", default=None)

_CONTEXT_VARS = {"request_id": request_id_var, "org_id": org_id_var, "workflow_id": workflow_id_var}
_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update({k: v.get() for k, v in _CONTEXT_VARS.items() if v.get()})
        # Fields passed as logger.info("...", extra={...}) become top-level keys.
        entry.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Uvicorn's own access log duplicates our request log line, without the ids.
    logging.getLogger("uvicorn.access").disabled = True


log = logging.getLogger("agentflow.http")


async def request_context_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Give every request an id (or keep the caller's X-Request-Id), log it once with its
    status and duration, and return the id so a user can quote it in a support ticket."""
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if 0 < len(incoming) <= 64 else uuid.uuid4().hex
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-Id"] = request_id
        return response
    finally:
        log.info(
            "http.request",
            extra={
                "method": request.method,
                "path": request.url.path,  # no query string: it may contain personal data
                "status": status,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            },
        )
        request_id_var.reset(token)
