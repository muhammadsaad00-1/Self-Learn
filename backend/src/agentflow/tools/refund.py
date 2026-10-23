"""The refund tool: the one write tool agents can trigger, and only through this function.

Order of checks (spec 03 §6): tenant-scoped session (caller) -> re-authorize with fresh data
(policy engine + approval) -> idempotency -> execute -> verify in the ledger -> record in the
action ledger -> audit. Everything happens in the caller's single database transaction, so
the refund, its action-ledger row and its audit event commit together or not at all.

ponytail: atomic because the ledger lives in our database. Against a real payment API the
same key goes to the provider as its Idempotency-Key, and the row is written before the
call (status in_progress) so a crash in between can be reconciled.
"""

import hashlib
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.agents.resolution import ActionType, ProposedAction
from agentflow.audit import service as audit
from agentflow.ledger import service as ledger
from agentflow.ledger.models import Transaction, TransactionStatus
from agentflow.ledger.providers import money_actions
from agentflow.policy import service as policy_service
from agentflow.policy.engine import Outcome, evaluate
from agentflow.tools.models import ActionExecution

TOOL = "refund_transaction"
TOOL_VERSION = "v1"


class ExecutionResult(BaseModel):
    status: Literal["executed", "not_executed"]
    detail: str
    refund_reference: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    verified: bool = False  # the ledger shows the debit reversed and the credit posted
    replayed: bool = False  # an earlier identical execution was returned (idempotency)


def action_key(workflow_id: uuid.UUID, target: str) -> str:
    """sha256(workflow + tool + logical target + tool version), spec 02 §7."""
    return hashlib.sha256(f"{workflow_id}:{TOOL}:{target}:{TOOL_VERSION}".encode()).hexdigest()


async def authorize_and_execute(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    workflow_id: uuid.UUID,
    proposal: ProposedAction,
    customer_ref: str | None,
    category: str | None,
    needs_human_review: bool,
    knowledge_status: str | None,
    approval_status: str | None,
) -> ExecutionResult:
    if proposal.action != ActionType.REFUND_TRANSACTION or not proposal.transaction_reference:
        return ExecutionResult(status="not_executed", detail="No action to execute")
    reference, amount = proposal.transaction_reference, proposal.amount or Decimal("0")

    key = action_key(workflow_id, reference)
    previous = await session.scalar(
        select(ActionExecution).where(
            ActionExecution.org_id == org_id, ActionExecution.idempotency_key == key
        )
    )
    if previous is not None:  # a retry after a crash, a duplicate run: never pay twice
        return ExecutionResult.model_validate(previous.result | {"replayed": True})

    # Re-authorize with fresh ledger data: limits may have changed since the proposal.
    policy_config, _ = await policy_service.get_config(session, org_id)
    evidence = await ledger.gather_evidence(session, org_id, customer_ref)
    decision = evaluate(
        proposal,
        evidence=evidence,
        category=category,
        needs_human_review=needs_human_review,
        knowledge_status=knowledge_status,
        config=policy_config,
    )
    if decision.outcome == Outcome.BLOCK:
        return ExecutionResult(status="not_executed", detail="; ".join(decision.reasons))
    if decision.outcome == Outcome.REQUIRE_APPROVAL and approval_status != "approved":
        return ExecutionResult(
            status="not_executed", detail=f"Approval {approval_status or 'missing'}"
        )

    try:
        posted = await money_actions.refund(
            session,
            org_id=org_id,
            customer_ref=customer_ref or "",
            transaction_reference=reference,
            amount=amount,
            idempotency_key=key,
        )
    except ledger.RefundNotPossibleError as e:
        return ExecutionResult(status="not_executed", detail=str(e))

    original = await session.scalar(
        select(Transaction).where(Transaction.org_id == org_id, Transaction.reference == reference)
    )
    credit = await session.scalar(
        select(Transaction).where(
            Transaction.org_id == org_id, Transaction.reference == posted.refund_reference
        )
    )
    verified = (
        original is not None
        and original.status == TransactionStatus.REVERSED
        and credit is not None
        and credit.amount == amount
    )
    result = ExecutionResult(
        status="executed",
        detail=f"Refunded {posted.amount} {posted.currency} for {reference}",
        refund_reference=posted.refund_reference,
        amount=posted.amount,
        currency=posted.currency,
        verified=verified,
    )
    session.add(
        ActionExecution(
            org_id=org_id,
            workflow_id=workflow_id,
            tool=TOOL,
            idempotency_key=key,
            request={"customer_ref": customer_ref, "reference": reference, "amount": str(amount)},
            result=result.model_dump(mode="json"),
        )
    )
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=None,
        action="tool.refund_executed",
        target_type="transaction",
        target_id=reference,
        details={
            "workflow_id": str(workflow_id),
            "amount": str(posted.amount),
            "currency": posted.currency,
            "refund_reference": posted.refund_reference,
            "policy_rules": decision.rules,
        },
    )
    return result
