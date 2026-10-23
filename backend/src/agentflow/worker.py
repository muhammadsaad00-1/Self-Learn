"""Background worker: runs queued workflows (agents) and document ingestion jobs.

    python -m agentflow.worker        (make worker / the `worker` service in docker compose)

Why not run this inside the API request? Model calls take seconds, providers rate-limit and
fail, embedding is CPU-heavy, and a crash must not lose work. So the API only records the
job (fast, durable) and returns 202; this loop picks it up and retries with backoff. Any
number of workers can run side by side: claiming uses FOR UPDATE SKIP LOCKED.

Database transactions are kept short and never span a model call: claim (commit), do the
slow work, then save results in their own small transactions.
"""

import asyncio
import contextlib
import logging
import signal
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentflow.agents import extraction
from agentflow.agents.resolution import ActionType, ProposedAction
from agentflow.approvals import service as approvals
from agentflow.audit import service as audit_service
from agentflow.config import Settings, get_settings
from agentflow.conversations import service as conversations
from agentflow.datasets import service as datasets
from agentflow.datasets.service import MAX_TEXT_CHARS
from agentflow.datasets.templates import RECORD_TYPES
from agentflow.db import create_engine, set_tenant_context
from agentflow.identity import service as identity
from agentflow.jobs import Claim
from agentflow.knowledge import ingestion, retrieval
from agentflow.knowledge import service as knowledge_service
from agentflow.knowledge.parsing import UnsupportedFileError, extract_text
from agentflow.knowledge.retrieval import RetrievedChunk
from agentflow.knowledge.runtime import KnowledgeRuntime, build_runtime
from agentflow.knowledge.storage import S3ObjectStore
from agentflow.ledger import service as ledger
from agentflow.ledger.schemas import Evidence
from agentflow.llm import ProviderUnavailableError, StructuredLLM, build_llm
from agentflow.observability import configure_logging, org_id_var, request_id_var, workflow_id_var
from agentflow.policy import service as policy_service
from agentflow.policy.engine import Outcome
from agentflow.tools import card as card_tool
from agentflow.tools import refund as refund_tool
from agentflow.tools import tickets as ticket_tool
from agentflow.tools.refund import ExecutionResult
from agentflow.tools.tickets import TicketRef
from agentflow.transport import service as transport
from agentflow.workflow import service
from agentflow.workflow.graph import (
    RECURSION_LIMIT,
    ExecuteFn,
    OpenTicketFn,
    ResearchFn,
    SearchFn,
    WorkflowGraphs,
    WorkflowState,
    build_workflow_graphs,
)
from agentflow.workflow.models import Workflow, WorkflowStage, WorkflowStatus

log = logging.getLogger("agentflow.worker")

Sessions = async_sessionmaker[AsyncSession]


def tenant_search(sessions: Sessions, org_id: uuid.UUID, runtime: KnowledgeRuntime) -> SearchFn:
    """Knowledge search bound to one tenant. Candidates are fetched in a short transaction;
    the reranker (CPU, ~1 s) runs after it, so no connection is held during inference."""

    async def search(queries: Sequence[str], rerank_query: str) -> list[RetrievedChunk]:
        async with sessions() as session, session.begin():
            await set_tenant_context(session, org_id=org_id)
            candidates = await retrieval.hybrid_candidates(
                session, org_id, queries, runtime.embedder, runtime.params
            )
        return await retrieval.rerank(
            runtime.reranker,
            rerank_query,
            candidates[: runtime.params.rerank_candidates],
            runtime.params.top_k,
        )

    return search


def tenant_research(sessions: Sessions, claim: Claim) -> ResearchFn:
    async def research(customer_ref: str | None) -> Evidence:
        async with sessions() as session, session.begin():
            await set_tenant_context(session, org_id=claim.org_id)
            evidence = await ledger.gather_evidence(session, claim.org_id, customer_ref)
            company = await identity.get_profile(session, claim.org_id)
            # Every time the models see is in the company's local time: "the 5 pm bus" is
            # 17:00+05:00 and a 2:41 am payment is 02:41, so no model does time-zone
            # arithmetic (QA: replies said "17:00 UTC", and "21:41" for a 2:41 am fraud).
            zone = ZoneInfo(company.profile.timezone if company.profile else "Asia/Karachi")
            evidence.local_today = datetime.now(zone).date()
            evidence.transactions = [
                t.model_copy(update={"occurred_at": t.occurred_at.astimezone(zone)})
                for t in evidence.transactions
            ]
            if company.capabilities.trips:
                evidence.trips = [
                    t.model_copy(
                        update={
                            "departs_at": t.departs_at.astimezone(zone),
                            "arrives_at": t.arrives_at.astimezone(zone) if t.arrives_at else None,
                        }
                    )
                    for t in await transport.upcoming(session, claim.org_id)
                ]
            if customer_ref:
                evidence.recent_requests_72h = await service.count_recent_for_customer(
                    session, claim.org_id, customer_ref, hours=72, excluding=claim.workflow_id
                )
            return evidence

    return research


def tenant_execute(sessions: Sessions, claim: Claim) -> ExecuteFn:
    """The write tools bound to this workflow's tenant. One transaction: the action, its
    action-ledger row and audit event commit together."""

    async def execute(state: WorkflowState) -> ExecutionResult:
        triage = state.get("triage") or {}
        proposal = ProposedAction.model_validate(state["proposal"])
        async with sessions() as session, session.begin():
            await set_tenant_context(session, org_id=claim.org_id)
            if proposal.action == ActionType.FREEZE_CARD:
                return await card_tool.freeze(
                    session,
                    org_id=claim.org_id,
                    workflow_id=claim.workflow_id,
                    proposal=proposal,
                    customer_ref=state.get("customer_ref"),
                    category=triage.get("category"),
                )
            return await refund_tool.authorize_and_execute(
                session,
                org_id=claim.org_id,
                workflow_id=claim.workflow_id,
                proposal=proposal,
                customer_ref=state.get("customer_ref"),
                category=triage.get("category"),
                needs_human_review=bool(triage.get("needs_human_review")),
                knowledge_status=(state.get("knowledge") or {}).get("status"),
                approval_status=(state.get("approval") or {}).get("status"),
            )

    return execute


def tenant_open_ticket(sessions: Sessions, claim: Claim) -> OpenTicketFn:
    async def open_ticket(state: WorkflowState, reasons: list[str]) -> TicketRef:
        async with sessions() as session, session.begin():
            await set_tenant_context(session, org_id=claim.org_id)
            return await ticket_tool.open_ticket(
                session,
                org_id=claim.org_id,
                workflow_id=claim.workflow_id,
                reasons=reasons,
                state=dict(state),
            )

    return open_ticket


async def _run(
    graph: CompiledStateGraph[WorkflowState],
    state: dict[str, Any],
    config: dict[str, Any],
    sessions: Sessions,
    claim: Claim,
) -> dict[str, Any]:
    """Stream a graph, saving each node's output as a workflow step as it finishes."""
    run_config: RunnableConfig = {"recursion_limit": RECURSION_LIMIT, "configurable": config}
    async for update in graph.astream(
        cast(WorkflowState, state),
        run_config,
        stream_mode="updates",  # yields {node_name: what that node returned}
    ):
        for node, output in update.items():
            meta = output.pop("node_meta", {})
            state.update(output)
            async with sessions() as session, session.begin():
                await set_tenant_context(session, org_id=claim.org_id)
                await service.record_step(session, claim, node, output, meta)
                if node == "triage":  # chat orchestration: a problem gets its own thread now
                    await conversations.after_triage(session, claim.workflow_id, output["triage"])
    return state


RESULT_KEYS = (
    "triage",
    "risk",
    "dispute",
    "knowledge",
    "evidence",
    "proposal",
    "decision",
    "approval",
    "execution",
    "ticket",
    "reply",
)


async def dead_lettered(session: AsyncSession, wf: Workflow, error: str) -> None:
    """The agents gave up (provider outage, a bug): a human must own the request, so open a
    ticket (exactly once) and tell the customer its case number. Without the ticket, "a
    specialist will reply" would be a promise nobody sees: a real provider outage (Groq's
    daily token limit) showed exactly that."""
    saved = dict(wf.result or {})
    state = {
        **saved,
        "message": service.agent_message(wf.input),
        "customer_message": wf.input["message"],
        "customer_ref": wf.input.get("customer_ref"),
        # Someone is waiting and nothing was resolved: high unless triage said more.
        "risk": saved.get("risk") or {"priority": "high"},
    }
    ticket = await ticket_tool.open_ticket(
        session,
        org_id=wf.org_id,
        workflow_id=wf.id,
        reasons=[f"Automation failed after {wf.attempts} attempts: {error[:300]}"],
        state=state,
    )
    await conversations.post_outcome(
        session, wf, {"ticket": ticket.model_dump(mode="json")}, "failed"
    )


async def process_next(
    sessions: Sessions, graphs: WorkflowGraphs, settings: Settings, runtime: KnowledgeRuntime
) -> bool:
    """Claim and run one workflow (its analysis or its execution stage). False = idle."""
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
        stage, saved, request_id = wf.stage, dict(wf.result or {}), wf.request_id
        message, customer_ref = service.agent_message(wf.input), wf.input.get("customer_ref")
        customer_message = wf.input["message"]  # what they actually wrote (tickets show it)
        approval = await approvals.get_for_workflow(session, wf.id)
        policy_config, _ = await policy_service.get_config(session, claim.org_id)
    request_id_var.set(request_id)  # logs of this run correlate with the API request
    log.info("workflow.started", extra={"attempt": claim.attempt, "stage": stage})

    config = {
        "search": tenant_search(sessions, claim.org_id, runtime),
        "min_rerank_score": runtime.min_rerank_score,
        "research": tenant_research(sessions, claim),
        "execute": tenant_execute(sessions, claim),
        "open_ticket": tenant_open_ticket(sessions, claim),
        "policy_config": policy_config,
    }
    try:
        async with asyncio.timeout(settings.workflow_timeout_seconds):
            if stage == WorkflowStage.ANALYSIS:
                state = await _run(
                    graphs.analysis,
                    {
                        "message": message,
                        "customer_message": customer_message,
                        "customer_ref": customer_ref,
                    },
                    config,
                    sessions,
                    claim,
                )
                decision = state.get("decision") or {}
                if decision.get("outcome") == Outcome.REQUIRE_APPROVAL:
                    async with sessions() as session, session.begin():
                        await set_tenant_context(session, org_id=claim.org_id)
                        if locked := await service.lock_if_still_ours(session, claim):
                            approval = await approvals.request_approval(
                                session, locked, state["proposal"], decision.get("reasons", [])
                            )
                            state["approval"] = {"id": str(approval.id), "status": "pending"}
                            result = {k: state.get(k) for k in RESULT_KEYS}
                            await service.pause_for_approval(session, locked, result)
                            await conversations.post_outcome(session, locked, state, "review")
                            log.info("workflow.waiting_approval")
                    return True
            else:  # execution stage, resumed after a human decided the approval
                state = {
                    **saved,
                    "message": message,
                    "customer_message": customer_message,
                    "customer_ref": customer_ref,
                }
                if approval is not None:
                    state["approval"] = {"id": str(approval.id), "status": approval.status}
            state = await _run(graphs.execution, state, config, sessions, claim)
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
                    outage=isinstance(e, ProviderUnavailableError),
                )
                if locked.status == WorkflowStatus.FAILED:
                    await dead_lettered(session, locked, f"{type(e).__name__}: {e}")
        return True

    async with sessions() as session, session.begin():
        await set_tenant_context(session, org_id=claim.org_id)
        if locked := await service.lock_if_still_ours(session, claim):
            await service.complete(session, locked, {k: state.get(k) for k in RESULT_KEYS})
            await conversations.post_outcome(session, locked, state, "reply")
            log.info("workflow.completed", extra={"attempt": claim.attempt})
    return True


async def process_next_document(
    sessions: Sessions, settings: Settings, runtime: KnowledgeRuntime
) -> bool:
    """Claim and ingest one document: download, parse, chunk, embed, swap chunks."""
    async with sessions() as session, session.begin():
        claim = await knowledge_service.claim_next_document(
            session,
            max_attempts=settings.document_max_attempts,
            lease_seconds=settings.document_lease_seconds,
        )
    if claim is None:
        return False

    org_id_var.set(str(claim.org_id))
    async with sessions() as session, session.begin():
        await set_tenant_context(session, org_id=claim.org_id)
        document = await knowledge_service.get_document(session, claim.org_id, claim.job_id)
        key, content_type = document.storage_key, document.content_type
    log.info("document.ingest_started", extra={"document_id": str(claim.job_id)})

    try:
        data = await runtime.store.get(key)
        prepared = await ingestion.prepare(data, content_type, runtime.embedder, runtime.screen)
    except Exception as e:
        log.exception("document.ingest_failed", extra={"attempt": claim.attempt})
        async with sessions() as session, session.begin():
            await set_tenant_context(session, org_id=claim.org_id)
            if locked := await knowledge_service.lock_if_still_ours(session, claim):
                await knowledge_service.fail_ingestion_attempt(
                    session,
                    locked,
                    claim,
                    f"{type(e).__name__}: {e}"[:2000],
                    max_attempts=(
                        claim.attempt
                        if isinstance(e, UnsupportedFileError)
                        else settings.document_max_attempts
                    ),
                )
        return True

    async with sessions() as session, session.begin():
        await set_tenant_context(session, org_id=claim.org_id)
        if locked := await knowledge_service.lock_if_still_ours(session, claim):
            await knowledge_service.complete_ingestion(session, locked, prepared)
            log.info("document.ingested", extra={"chunks": len(prepared.drafts)})
    return True


async def process_next_import(sessions: Sessions, llm: StructuredLLM, settings: Settings) -> bool:
    """Claim one report (PDF/text import) and let the extraction agent list its rows. The
    rows go to REVIEW: people check them before anything is committed."""
    async with sessions() as session, session.begin():
        claim = await datasets.claim_next_extraction(
            session,
            max_attempts=settings.document_max_attempts,
            lease_seconds=settings.document_lease_seconds,
        )
    if claim is None:
        return False
    org_id_var.set(str(claim.org_id))
    async with sessions() as session, session.begin():
        await set_tenant_context(session, org_id=claim.org_id)
        batch = await datasets.get_batch(session, claim.org_id, claim.job_id)
        record_type = batch.record_type
        report = batch.source_text or ""
        source_data = batch.source_data
        source_content_type = batch.source_content_type
    try:
        if source_data is not None and source_content_type is not None:
            report = await asyncio.to_thread(extract_text, source_data, source_content_type)
        report = report[:MAX_TEXT_CHARS]
        rows, _calls, notes = await extraction.extract(llm, RECORD_TYPES[record_type], report)
    except Exception as e:
        log.exception("import.extraction_failed", extra={"attempt": claim.attempt})
        async with sessions() as session, session.begin():
            await set_tenant_context(session, org_id=claim.org_id)
            if locked := await datasets.lock_if_still_ours(session, claim):
                await datasets.fail_extraction(
                    session,
                    locked,
                    claim,
                    f"{type(e).__name__}: {e}"[:2000],
                    max_attempts=(
                        claim.attempt
                        if isinstance(e, UnsupportedFileError)
                        else settings.document_max_attempts
                    ),
                )
        return True
    async with sessions() as session, session.begin():
        await set_tenant_context(session, org_id=claim.org_id)
        if locked := await datasets.lock_if_still_ours(session, claim):
            await datasets.complete_extraction(session, locked, rows, notes)
            log.info("import.extracted", extra={"rows": len(rows)})
    return True


async def sweep_expired_approvals(sessions: Sessions) -> int:
    """Expire approvals nobody decided in time (their workflows resume and report "not
    executed"), and audit each expiry in the same transaction. Returns how many expired."""
    async with sessions() as session, session.begin():
        expired = (await session.execute(text("SELECT * FROM expire_approvals()"))).all()
        for item in expired:
            await set_tenant_context(session, org_id=item.org_id)
            await audit_service.record(
                session,
                org_id=item.org_id,
                actor_user_id=None,
                action="approval.expired",
                target_type="approval",
                target_id=item.approval_id,
                details={"workflow_id": str(item.workflow_id)},
            )
    return len(expired)


async def run(settings: Settings, stop: asyncio.Event, poll_seconds: float = 1.0) -> None:
    engine = create_engine(str(settings.database_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    runtime = build_runtime(settings)
    if settings.env == "local" and isinstance(runtime.store, S3ObjectStore):
        await runtime.store.ensure_bucket()
    llm = build_llm(settings)
    graphs = build_workflow_graphs(llm)
    # Load the embedding and reranker models now (seconds; a download on first run), not
    # inside the first customer's request where it ate the workflow's time budget.
    await runtime.embedder.embed_query("warm up")
    await runtime.reranker.score("warm up", ["warm up"])
    log.info("worker.started")
    try:
        while not stop.is_set():
            # Ingestion first: an answer can only be as good as the indexed documents.
            busy = await process_next_document(sessions, settings, runtime)
            busy = await process_next_import(sessions, llm, settings) or busy
            async with sessions() as session, session.begin():
                expired_holds = (
                    await session.execute(text("SELECT * FROM expire_seat_holds()"))
                ).all()
                for hold in expired_holds:
                    org_id = hold.org_id
                    await set_tenant_context(session, org_id=org_id)
                    await audit_service.record(
                        session,
                        org_id=org_id,
                        actor_user_id=None,
                        action="booking.hold_expired",
                        target_type="seat_booking",
                        target_id=hold.booking_id,
                    )
                busy = bool(expired_holds) or busy
            # Approvals nobody decided in time expire (and their workflows resume).
            await sweep_expired_approvals(sessions)
            busy = await process_next(sessions, graphs, settings, runtime) or busy
            if not busy:
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
        # Docker/ECS send SIGTERM on deploy: finish the current job, then exit.
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await run(settings, stop)

    asyncio.run(_main())


if __name__ == "__main__":
    main()
