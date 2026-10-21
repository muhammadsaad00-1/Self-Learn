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
    if proposal.action == ActionType.NO_ACTION:
        return PolicyDecision(
            outcome=Outcome.ALLOW, reasons=["No action to execute"], rules=["no_action"]
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
    if amount > config.auto_approve_limit:
        approvals.append(
            (
                "maker_checker",
                f"Refunds above {config.auto_approve_limit} need a second officer's approval",
            )
        )
    if needs_human_review:
        approvals.append(("triage_review", "Triage flagged this request for human review"))
    if knowledge_status != "answered":
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
    return PolicyDecision(
        outcome=Outcome.ALLOW,
        reasons=[
            f"Refund of {amount} is within the automatic limit of {config.auto_approve_limit}"
        ],
        rules=["auto_approve"],
    )
