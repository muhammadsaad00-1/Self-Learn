"""Workflow use cases: create (idempotently), read, and the state changes the worker makes."""

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.audit import service as audit
from agentflow.jobs import Claim, backoff
from agentflow.workflow.models import StepStatus, Workflow, WorkflowStatus, WorkflowStep
from agentflow.workflow.schemas import CreateWorkflowRequest, WorkflowSummary

log = logging.getLogger("agentflow.workflow")


class WorkflowNotFoundError(Exception):
    pass


class IdempotencyConflictError(Exception):
    """The Idempotency-Key was already used for a *different* request."""


def agent_message(workflow_input: dict[str, Any]) -> str:
    """What the agents read: the customer's message, after the conversation so far."""
    message: str = workflow_input["message"]
    if context := workflow_input.get("context"):
        return (
            f"Earlier in this chat (context only):\n{context}\n\nCustomer's new message:\n{message}"
        )
    return message


# --- Creating and reading --------------------------------------------------------------


def fingerprint(body: CreateWorkflowRequest) -> str:
    """Stable hash of the request body: same fields and values, same fingerprint."""
    canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _by_key(session: AsyncSession, org_id: uuid.UUID, key: str) -> Workflow | None:
    return await session.scalar(
        select(Workflow).where(Workflow.org_id == org_id, Workflow.idempotency_key == key)
    )


def _replay(existing: Workflow, fp: str) -> Workflow:
    if existing.request_fingerprint != fp:
        raise IdempotencyConflictError
    return existing


async def create(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,  # None: a customer channel (chat), not a staff member
    idempotency_key: str,
    body: CreateWorkflowRequest,
    request_id: str | None,
) -> tuple[Workflow, bool]:
    """Queue a workflow, or return the one already created with this key: (workflow, created).

    A client that times out and retries with the same key gets the original workflow back
    instead of a duplicate, so one complaint can never be processed twice (spec 02 §7).
    """
    fp = fingerprint(body)
    if existing := await _by_key(session, org_id, idempotency_key):
        return _replay(existing, fp), False
    workflow = Workflow(
        org_id=org_id,
        created_by=user_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fp,
        request_id=request_id,
        input=body.model_dump(mode="json"),
    )
    try:
        async with session.begin_nested():
            session.add(workflow)
    except IntegrityError as e:
        # Two requests with the same key raced past the check above; the other one won.
        if "uq_workflows_org_id_idempotency_key" not in str(e.orig):
            raise
        winner = await _by_key(session, org_id, idempotency_key)
        assert winner is not None  # noqa: S101 (the unique violation proves it exists)
        return _replay(winner, fp), False
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=user_id,
        action="workflow.created",
        target_type="workflow",
        target_id=workflow.id,
        details={"channel": body.channel},
    )
    return workflow, True


async def get(session: AsyncSession, org_id: uuid.UUID, workflow_id: uuid.UUID) -> Workflow:
    workflow = await session.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.org_id == org_id)
    )
    if workflow is None:
        raise WorkflowNotFoundError
    return workflow


async def steps_of(session: AsyncSession, workflow_id: uuid.UUID) -> list[WorkflowStep]:
    rows = await session.scalars(
        select(WorkflowStep)
        .where(WorkflowStep.workflow_id == workflow_id)
        .order_by(WorkflowStep.created_at)
    )
    return list(rows)


async def list_recent(
    session: AsyncSession, org_id: uuid.UUID, limit: int = 50
) -> list[WorkflowSummary]:
    rows = await session.scalars(
        select(Workflow)
        .where(Workflow.org_id == org_id)
        .order_by(Workflow.created_at.desc())
        .limit(limit)
    )
    summaries = []
    for wf in rows:
        triage = (wf.result or {}).get("triage") or {}
        summaries.append(
            WorkflowSummary(
                id=wf.id,
                status=wf.status,
                message_preview=wf.input["message"][:120],
                customer_ref=wf.input.get("customer_ref"),
                channel=wf.input.get("channel", "web"),
                category=triage.get("category"),
                urgency=triage.get("urgency"),
                needs_human_review=triage.get("needs_human_review"),
                created_at=wf.created_at,
            )
        )
    return summaries


# --- Worker side ------------------------------------------------------------------------


async def claim_next(
    session: AsyncSession, *, max_attempts: int, lease_seconds: int
) -> Claim | None:
    """Take the next due workflow from any org (see the SECURITY DEFINER function)."""
    row = (
        await session.execute(
            text("SELECT * FROM claim_next_workflow(:max_attempts, :lease_seconds)"),
            {"max_attempts": max_attempts, "lease_seconds": lease_seconds},
        )
    ).one_or_none()
    return Claim(row.claimed_id, row.claimed_org_id, row.claimed_attempt) if row else None


async def lock_if_still_ours(session: AsyncSession, claim: Claim) -> Workflow | None:
    """Lock the workflow row, but only if this worker still holds the lease.

    If our lease expired (say the model call hung) another worker may have claimed the
    workflow again and bumped `attempts`; our late result must then be thrown away, not
    written over theirs.
    """
    wf = await session.scalar(
        select(Workflow).where(Workflow.id == claim.workflow_id).with_for_update()
    )
    if wf is None or wf.status != WorkflowStatus.RUNNING or wf.attempts != claim.attempt:
        log.warning("workflow.lease_lost", extra={"attempt": claim.attempt})
        return None
    return wf


async def record_step(
    session: AsyncSession, claim: Claim, node: str, output: dict[str, Any], meta: dict[str, Any]
) -> None:
    session.add(
        WorkflowStep(
            org_id=claim.org_id,
            workflow_id=claim.workflow_id,
            node=node,
            attempt=claim.attempt,
            status=StepStatus.COMPLETED,
            output=output,
            model=meta.get("model"),
            input_tokens=meta.get("input_tokens"),
            output_tokens=meta.get("output_tokens"),
            latency_ms=meta.get("latency_ms"),
        )
    )


async def pause_for_approval(session: AsyncSession, wf: Workflow, result: dict[str, Any]) -> None:
    """Save the analysis and park the workflow until a human decides (approvals.service)."""
    wf.status = WorkflowStatus.WAITING_APPROVAL
    wf.result = result
    wf.locked_until = None
    wf.error = None


async def complete(session: AsyncSession, wf: Workflow, result: dict[str, Any]) -> None:
    wf.status = WorkflowStatus.COMPLETED
    wf.result = result
    wf.error = None
    wf.locked_until = None
    wf.ended_at = func.now()
    await audit.record(
        session,
        org_id=wf.org_id,
        actor_user_id=None,  # the system acted
        action="workflow.completed",
        target_type="workflow",
        target_id=wf.id,
    )


async def fail_attempt(
    session: AsyncSession, wf: Workflow, claim: Claim, error: str, *, max_attempts: int
) -> None:
    """Record a failed attempt: retry later with backoff, or give up (dead-letter)."""
    session.add(
        WorkflowStep(
            org_id=claim.org_id,
            workflow_id=claim.workflow_id,
            node="attempt",
            attempt=claim.attempt,
            status=StepStatus.FAILED,
            error=error,
        )
    )
    wf.error = error
    wf.locked_until = None
    if claim.attempt >= max_attempts:
        wf.status = WorkflowStatus.FAILED
        wf.ended_at = func.now()
        await audit.record(
            session,
            org_id=wf.org_id,
            actor_user_id=None,
            action="workflow.failed",
            target_type="workflow",
            target_id=wf.id,
            details={"attempts": claim.attempt},
        )
    else:
        wf.status = WorkflowStatus.QUEUED
        wf.available_at = datetime.now(UTC) + backoff(claim.attempt)


async def count_recent_for_customer(
    session: AsyncSession,
    org_id: uuid.UUID,
    customer_ref: str,
    *,
    hours: int,
    excluding: uuid.UUID,
) -> int:
    """Requests from this customer in the last `hours` (repeat-contact signal)."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    count = await session.scalar(
        select(func.count())
        .select_from(Workflow)
        .where(
            Workflow.org_id == org_id,
            Workflow.input["customer_ref"].astext == customer_ref,
            Workflow.created_at >= since,
            Workflow.id != excluding,
        )
    )
    return count or 0
