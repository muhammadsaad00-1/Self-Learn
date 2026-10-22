"""The policy engine: (proposed action, context) -> allow | require_approval | block.

Evaluated twice: once when the action is proposed, and again right before execution with
fresh ledger data. Limits can change while an approval waits (another refund may have been
posted meanwhile), so an approval is permission to *try*, not a blank cheque.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from agentflow.agents.resolution import ActionType, ProposedAction
from agentflow.ledger.models import TransferStatus
from agentflow.ledger.schemas import Evidence


class Outcome(StrEnum):
    ALLOW = "allow"  # execute automatically
    REQUIRE_APPROVAL = "require_approval"  # a human (not the submitter) must approve
    BLOCK = "block"  # no automated execution; a human handles the case


@dataclass(frozen=True)
class PolicyConfig:
    """Defaults mirror the EasyPay refund policy in the eval corpus (maker-checker section).
    ponytail: one config for all orgs; per-org policies table when a second customer needs
    different limits."""

    auto_approve_limit: Decimal = Decimal("2000")  # refunds above need a second officer
    max_refund: Decimal = Decimal("100000")  # above: never automated, even with approval
    max_reversals_per_day: int = 3


class PolicyDecision(BaseModel):
    outcome: Outcome
    reasons: list[str]  # human-readable, one per rule that fired
    rules: list[str]  # stable rule ids, for audit queries


def evaluate(
    proposal: ProposedAction,
    *,
    evidence: Evidence,
    category: str | None,
    needs_human_review: bool,
    knowledge_status: str | None,
    config: PolicyConfig = PolicyConfig(),  # noqa: B008 (immutable)
) -> PolicyDecision:
    if proposal.action == ActionType.ANSWER_FROM_RECORDS:
        return PolicyDecision(
            outcome=Outcome.ALLOW,
            reasons=["Answered from the customer's own records (read-only)"],
            rules=["records_answer"],
        )
    if proposal.action == ActionType.NO_ACTION:
        return PolicyDecision(
            outcome=Outcome.ALLOW, reasons=["No action to execute"], rules=["no_action"]
        )
    if proposal.action == ActionType.FREEZE_CARD:
        return PolicyDecision(
            outcome=Outcome.ALLOW,
            reasons=["Freezing a card is protective and reversible: done at once on fraud"],
            rules=["protective_freeze"],
        )
    if proposal.action == ActionType.ESCALATE_TO_HUMAN:
        return PolicyDecision(
            outcome=Outcome.BLOCK,
            reasons=["Escalated to a human by the agents"],
            rules=["escalation"],
        )

    amount = proposal.amount or Decimal("0")
    txn = next(
        (t for t in evidence.transactions if t.reference == proposal.transaction_reference), None
    )
    # The payment network itself confirmed the transfer failed: the money is owed back, so
    # the reversal doesn't wait for a second officer or a policy passage.
    network_failed = (
        category == "failed_payment"
        and txn is not None
        and txn.transfer_status == TransferStatus.FAILED
    )
    blocks: list[tuple[str, str]] = []
    approvals: list[tuple[str, str]] = []

    if amount > config.max_refund:
        blocks.append(
            (
                "max_refund",
                f"Refund of {amount} exceeds the automated ceiling of {config.max_refund}",
            )
        )
    if evidence.reversals_last_24h >= config.max_reversals_per_day:
        blocks.append(
            (
                "daily_reversals",
                f"Customer already had {evidence.reversals_last_24h} reversals in 24 hours "
                f"(max {config.max_reversals_per_day})",
            )
        )
    if txn is None:
        blocks.append(("unknown_transaction", "The transaction is not in the customer's ledger"))
    if category == "unauthorized_transaction":
        approvals.append(("fraud_review", "Suspected fraud always needs human review"))
    if amount > config.auto_approve_limit and not network_failed:
        approvals.append(
            (
                "maker_checker",
                f"Refunds above {config.auto_approve_limit} need a second officer's approval",
            )
        )
    if needs_human_review:
        approvals.append(("triage_review", "Triage flagged this request for human review"))
    if knowledge_status != "answered" and not network_failed:
        approvals.append(("no_policy_basis", "No verified policy answer supports this refund"))

    if blocks:
        return PolicyDecision(
            outcome=Outcome.BLOCK, reasons=[r for _, r in blocks], rules=[i for i, _ in blocks]
        )
    if approvals:
        return PolicyDecision(
            outcome=Outcome.REQUIRE_APPROVAL,
            reasons=[r for _, r in approvals],
            rules=[i for i, _ in approvals],
        )
    if network_failed:
        return PolicyDecision(
            outcome=Outcome.ALLOW,
            reasons=[f"The network reports the transfer failed: {amount} is owed back"],
            rules=["failed_transfer_reversal"],
        )
    return PolicyDecision(
        outcome=Outcome.ALLOW,
        reasons=[
            f"Refund of {amount} is within the automatic limit of {config.auto_approve_limit}"
        ],
        rules=["auto_approve"],
    )


def ticket_reasons(
    *,
    category: str | None,
    urgency: str | None,
    decision: PolicyDecision | None,
    approval_status: str | None,
    execution_status: str | None,
    execution_detail: str | None,
    risk_critical: bool = False,
    risk_signals: list[str] | None = None,
) -> list[str]:
    """Why a human must own this case as a ticket (empty list = no ticket).

    Approval-required actions don't get one while pending: the approvals inbox owns them.
    A ticket is for outcomes that left the case unresolved or that are critical anyway.
    """
    reasons = []
    if category == "unauthorized_transaction":
        reasons.append("Suspected fraud: investigate and contact the customer")
    if urgency == "critical":
        reasons.append("Critical urgency")
    elif risk_critical:
        reasons.append("Critical risk: " + "; ".join(risk_signals or []))
    if decision is not None and decision.outcome == Outcome.BLOCK:
        reasons.append("Not automated: " + "; ".join(decision.reasons))
    if approval_status in ("rejected", "expired"):
        reasons.append(f"Approval {approval_status}: the customer still needs a resolution")
    if approval_status == "approved" and execution_status == "not_executed":
        reasons.append(f"Approved action could not be executed: {execution_detail}")
    return reasons
