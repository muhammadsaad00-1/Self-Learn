"""Workflow worker: a separate process that runs queued workflows.

    python -m agentflow.worker        (make worker / the `worker` service in docker compose)

Why not run the agents inside the API request? A model call can take seconds, providers
rate-limit and fail, and a crash must not lose work. So the API only records the workflow
(fast, durable) and returns 202; this loop picks it up, runs the graph, and retries with
backoff. Any number of workers can run side by side: claiming uses FOR UPDATE SKIP LOCKED.

Database transactions are kept short and never span a model call: claim (commit), run the
graph, then save each step and the outcome in their own small transactions.
"""

import asyncio
import contextlib
import logging
import signal
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentflow.config import Settings, get_settings
from agentflow.db import create_engine, set_tenant_context
from agentflow.llm import build_llm
from agentflow.observability import configure_logging, org_id_var, request_id_var, workflow_id_var
from agentflow.workflow import service
from agentflow.workflow.graph import RECURSION_LIMIT, WorkflowState, build_graph

log = logging.getLogger("agentflow.worker")

Sessions = async_sessionmaker[AsyncSession]


async def process_next(
    sessions: Sessions, graph: CompiledStateGraph[WorkflowState], settings: Settings
) -> bool:
    """Claim and run one workflow. Returns False when there was nothing to do."""
    async with sessions() as session, session.begin():
        claim = await service.claim_next(
            session,
            max_attempts=settings.workflow_max_attempts,
            lease_seconds=settings.workflow_lease_seconds,
        )
    if claim is None:
        return False

    org_id_var.set(str(claim.org_id))
    workflow_id_var.set(str(claim.workflow_id))
    async with sessions() as session, session.begin():
        await set_tenant_context(session, org_id=claim.org_id)
        wf = await service.get(session, claim.org_id, claim.workflow_id)
        message, request_id = wf.input["message"], wf.request_id
    request_id_var.set(request_id)  # logs of this run correlate with the API request
    log.info("workflow.started", extra={"attempt": claim.attempt})

    state: dict[str, Any] = {}
    try:
        # Wall-clock budget for the whole graph, on top of per-call model timeouts.
        async with asyncio.timeout(settings.workflow_timeout_seconds):
            async for update in graph.astream(
                {"message": message},
                stream_mode="updates",  # yields {node_name: what that node returned}
                config={"recursion_limit": RECURSION_LIMIT},
            ):
                for node, output in update.items():
                    meta = output.pop("node_meta", {})
                    state.update(output)
                    async with sessions() as session, session.begin():
                        await set_tenant_context(session, org_id=claim.org_id)
                        await service.record_step(session, claim, node, output, meta)
    except Exception as e:  # any failure: model, validation, timeout, bug
        log.exception("workflow.attempt_failed", extra={"attempt": claim.attempt})
        async with sessions() as session, session.begin():
            await set_tenant_context(session, org_id=claim.org_id)
            if locked := await service.lock_if_still_ours(session, claim):
                await service.fail_attempt(
                    session,
                    locked,
                    claim,
                    f"{type(e).__name__}: {e}"[:2000],
                    max_attempts=settings.workflow_max_attempts,
                )
        return True

    async with sessions() as session, session.begin():
        await set_tenant_context(session, org_id=claim.org_id)
        if locked := await service.lock_if_still_ours(session, claim):
            await service.complete(session, locked, {"triage": state.get("triage")})
            log.info("workflow.completed", extra={"attempt": claim.attempt})
    return True


async def run(settings: Settings, stop: asyncio.Event, poll_seconds: float = 1.0) -> None:
    engine = create_engine(str(settings.database_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    graph = build_graph(build_llm(settings))
    log.info("worker.started")
    try:
        while not stop.is_set():
            if not await process_next(sessions, graph, settings):
                # Idle: wait before polling again, but wake at once on shutdown.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
    finally:
        await engine.dispose()
        log.info("worker.stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        # Docker/ECS send SIGTERM on deploy: finish the current workflow, then exit.
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(settings, stop)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
