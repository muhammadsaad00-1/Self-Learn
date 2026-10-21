"""Deterministic gatekeepers: duplicate detection, proposal validation, the policy engine."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agentflow.agents.resolution import ActionType, validate
from agentflow.ledger.models import TransactionKind, TransactionStatus
from agentflow.ledger.schemas import Evidence, TransactionOut
from agentflow.ledger.service import find_duplicates
from agentflow.policy.engine import Outcome, evaluate
from tests.fakes import proposal

T0 = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


def txn(
    ref: str, amount: str, seconds: int = 0, merchant: str = "KE-Bills", **kw: object
) -> TransactionOut:
    return TransactionOut(
        id=uuid.uuid4(),
        reference=ref,
        kind=kw.get("kind", TransactionKind.DEBIT),
        amount=Decimal(amount),
        currency="PKR",
        merchant=merchant,
        status=kw.get("status", TransactionStatus.POSTED),
        occurred_at=T0 + timedelta(seconds=seconds),
    )


def evidence(*txns: TransactionOut, reversals: int = 0) -> Evidence:
    return Evidence(
        customer_ref="CUST-1",
        customer_name="Ayesha",
        account_found=True,
        transactions=list(txns),
        duplicate_pairs=[(a.reference, b.reference) for a, b in find_duplicates(list(txns))],
        reversals_last_24h=reversals,
    )


# --- Duplicate detection -----------------------------------------------------------------


def test_same_amount_and_merchant_within_ten_minutes_is_a_duplicate() -> None:
    pairs = find_duplicates([txn("A", "5000", 0), txn("B", "5000", 40), txn("C", "5000", 7200)])
    assert [(a.reference, b.reference) for a, b in pairs] == [("A", "B")]  # C: hours later


@pytest.mark.parametrize(
    "second",
    [
        txn("B", "4999", 40),  # different amount
        txn("B", "5000", 40, merchant="Daraz"),  # different merchant
        txn("B", "5000", 40, kind=TransactionKind.CREDIT),  # not a debit
        txn("B", "5000", 601),  # just outside the window
    ],
)
def test_near_misses_are_not_duplicates(second: TransactionOut) -> None:
    assert find_duplicates([txn("A", "5000", 0), second]) == []


def test_three_identical_debits_give_one_pair_not_three() -> None:
    pairs = find_duplicates([txn("A", "100", 0), txn("B", "100", 10), txn("C", "100", 20)])
    assert [(a.reference, b.reference) for a, b in pairs] == [("A", "B")]


# --- Proposal validation (the model proposes, code checks) --------------------------------

EVIDENCE = evidence(txn("TXN-1", "5000", 0), txn("TXN-2", "5000", 40))


def test_valid_duplicate_refund_passes() -> None:
    p = validate(proposal("TXN-2", "5000"), EVIDENCE, "duplicate_charge")
    assert (p.action, p.transaction_reference, p.amount) == (
        ActionType.REFUND_TRANSACTION,
        "TXN-2",
        Decimal("5000"),
    )


@pytest.mark.parametrize(
    ("p", "category", "why"),
    [
        (proposal("TXN-9", "5000"), "duplicate_charge", "not in the evidence"),  # hallucinated
        (proposal("TXN-2", "9000"), "duplicate_charge", "not between 0 and 5000"),  # too much
        (proposal("TXN-2", "-1"), "duplicate_charge", "not between 0 and 5000"),
        (proposal("TXN-2", None), "duplicate_charge", "not between"),
        (proposal("TXN-1", "5000"), "duplicate_charge", "not the duplicate debit"),  # original
        (proposal("TXN-2", "5000"), "unauthorized_transaction", "not automated"),  # fraud
    ],
)
def test_invalid_refunds_become_escalations(p: object, category: str, why: str) -> None:
    result = validate(p, EVIDENCE, category)  # type: ignore[arg-type]
    assert result.action == ActionType.ESCALATE_TO_HUMAN
    assert why in result.rationale
    assert result.amount is None


def test_amount_is_normalized_to_two_decimals() -> None:
    assert validate(proposal("TXN-2", "5000.0"), EVIDENCE, "duplicate_charge").amount == Decimal(
        "5000.00"
    )
    assert str(validate(proposal("TXN-2", "1200.004"), EVIDENCE, "duplicate_charge").amount) == (
        "1200.00"
    )


def test_already_reversed_debit_cannot_be_refunded() -> None:
    reversed_evidence = evidence(
        txn("TXN-1", "50", 0), txn("TXN-2", "50", 40, status=TransactionStatus.REVERSED)
    )
    assert validate(proposal("TXN-2", "50"), reversed_evidence, "duplicate_charge").action == (
        ActionType.ESCALATE_TO_HUMAN
    )


# --- Policy engine -------------------------------------------------------------------------


def decide(
    amount: str,
    *,
    category: str = "duplicate_charge",
    review: bool = False,
    knowledge: str = "answered",
    reversals: int = 0,
) -> tuple[Outcome, list[str]]:
    ev = evidence(txn("TXN-1", amount, 0), txn("TXN-2", amount, 40), reversals=reversals)
    d = evaluate(
        proposal("TXN-2", amount),
        evidence=ev,
        category=category,
        needs_human_review=review,
        knowledge_status=knowledge,
    )
    return d.outcome, d.rules


@pytest.mark.parametrize(
    ("kwargs", "outcome", "rules"),
    [
        ({"amount": "1200"}, Outcome.ALLOW, ["auto_approve"]),
        ({"amount": "2000"}, Outcome.ALLOW, ["auto_approve"]),  # the limit itself is allowed
        ({"amount": "2000.01"}, Outcome.REQUIRE_APPROVAL, ["maker_checker"]),
        ({"amount": "1200", "review": True}, Outcome.REQUIRE_APPROVAL, ["triage_review"]),
        (
            {"amount": "1200", "knowledge": "abstained"},
            Outcome.REQUIRE_APPROVAL,
            ["no_policy_basis"],
        ),
        (
            {"amount": "1200", "category": "unauthorized_transaction"},
            Outcome.REQUIRE_APPROVAL,
            ["fraud_review"],
        ),
        ({"amount": "150000"}, Outcome.BLOCK, ["max_refund"]),
        ({"amount": "1200", "reversals": 3}, Outcome.BLOCK, ["daily_reversals"]),
        # Blocks win over approvals: no human can approve past a hard limit.
        ({"amount": "150000", "review": True}, Outcome.BLOCK, ["max_refund"]),
    ],
)
def test_policy_rules(kwargs: dict[str, object], outcome: Outcome, rules: list[str]) -> None:
    assert decide(**kwargs) == (outcome, rules)  # type: ignore[arg-type]


def test_escalation_and_no_action() -> None:
    ev = evidence()
    escalate = proposal(action="escalate_to_human")
    nothing = proposal(action="no_action")
    kw = {
        "evidence": ev,
        "category": "other",
        "needs_human_review": False,
        "knowledge_status": None,
    }
    assert evaluate(escalate, **kw).outcome == Outcome.BLOCK  # type: ignore[arg-type]
    assert evaluate(nothing, **kw).outcome == Outcome.ALLOW  # type: ignore[arg-type]
