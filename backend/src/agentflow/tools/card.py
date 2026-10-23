"""The card-freeze tool: protective, reversible, and allowed without approval on suspected
fraud (policy rule `protective_freeze`). Same contract as the refund tool: re-authorize,
exactly once through the action ledger, audit, all in the caller's transaction."""

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.agents.resolution import ProposedAction
from agentflow.audit import service as audit
from agentflow.ledger import service as ledger
from agentflow.policy import dispute
from agentflow.policy.engine import Outcome, evaluate
from agentflow.tools.models import ActionExecution
from agentflow.tools.refund import ExecutionResult

TOOL = "freeze_card"


async def freeze(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    workflow_id: uuid.UUID,
    proposal: ProposedAction,
    customer_ref: str | None,
    category: str | None,
) -> ExecutionResult:
    key = hashlib.sha256(f"{workflow_id}:{TOOL}:{customer_ref}:v1".encode()).hexdigest()
    previous = await session.scalar(
        select(ActionExecution).where(
            ActionExecution.org_id == org_id, ActionExecution.idempotency_key == key
        )
    )
    if previous is not None:
        return ExecutionResult.model_validate(previous.result | {"replayed": True})

    evidence = await ledger.gather_evidence(session, org_id, customer_ref)
    decision = evaluate(
        proposal,
        evidence=evidence,
        category=category,
        needs_human_review=False,
        knowledge_status=None,
        # Re-assessed on fresh ledger data, like every other check at execution time.
        dispute_verdict=dispute.assess(evidence, proposal.record_references).verdict,
    )
    if decision.outcome != Outcome.ALLOW or not evidence.account_found:
        return ExecutionResult(
            status="not_executed", detail="; ".join(decision.reasons) or "Not authorized"
        )

    account = await ledger.get_account(session, org_id, customer_ref or "")
    was_active = await ledger.freeze_card(session, account)
    result = ExecutionResult(
        status="executed",
        detail="Card frozen" if was_active else "Card was already frozen",
        verified=True,
    )
    session.add(
        ActionExecution(
            org_id=org_id,
            workflow_id=workflow_id,
            tool=TOOL,
            idempotency_key=key,
            request={"customer_ref": customer_ref},
            result=result.model_dump(mode="json"),
        )
    )
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=None,
        action="tool.card_frozen",
        target_type="account",
        target_id=customer_ref or "",
        details={"workflow_id": str(workflow_id), "policy_rules": decision.rules},
    )
    return result
