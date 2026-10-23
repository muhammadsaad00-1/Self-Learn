"""Dispute intelligence: when a customer says "I never made this payment", what does the
evidence say?

Two very different cases look the same in a message:
- genuine fraud (account takeover, stolen card): protect the customer at once;
- first-party ("friendly") fraud or a forgotten purchase: the customer made it.

Card systems already record what settles it: the device, whether OTP / 3-D Secure passed,
whether the merchant is one the customer uses. This module weighs those signals in code (the
model never decides it) and names each one, so the ticket, the reviewer and the customer's
reply all rest on the same facts. It never accuses: "customer-made" means a specialist reviews
before any freeze or refund, and the customer is shown what the records say.
"""

from collections.abc import Sequence
from decimal import Decimal
from statistics import median
from typing import Literal

from pydantic import BaseModel

from agentflow.ledger.models import TransactionKind
from agentflow.ledger.schemas import Evidence, TransactionOut

Verdict = Literal["likely_unauthorized", "likely_customer_made", "unclear"]


class DisputeSignal(BaseModel):
    reference: str
    signal: str  # stable id
    points_unauthorized: int  # evidence the customer did NOT make it
    points_customer: int  # evidence the customer made it
    detail: str


class DisputeAssessment(BaseModel):
    verdict: Verdict
    disputed: list[str]  # transaction references assessed
    unauthorized_score: int
    customer_score: int
    signals: list[DisputeSignal]
    summary: str  # one line for people and for the reply agent


MARGIN = 30  # points one side needs over the other for a verdict


def _signals(t: TransactionOut, history: list[TransactionOut]) -> list[DisputeSignal]:
    out: list[DisputeSignal] = []

    def add(signal: str, unauthorized: int, customer: int, detail: str) -> None:
        out.append(
            DisputeSignal(
                reference=t.reference,
                signal=signal,
                points_unauthorized=unauthorized,
                points_customer=customer,
                detail=detail,
            )
        )

    if t.verified is True:
        add("otp_verified", 0, 40, "confirmed with an OTP / 3-D Secure sent to the customer")
    elif t.verified is False:
        add("not_verified", 30, 0, "no OTP / 3-D Secure confirmation")
    if t.device_known is True:
        add("registered_device", 0, 30, f"made from the customer's {t.device or 'device'}")
    elif t.device_known is False:
        add("unrecognised_device", 40, 0, t.device or "an unrecognised device")

    earlier = [h for h in history if h.merchant == t.merchant and h.occurred_at < t.occurred_at]
    if len(earlier) >= 2:
        add("known_merchant", 0, 20, f"{len(earlier)} earlier payments to {t.merchant}")
    elif not earlier:
        add("new_merchant", 15, 0, f"first ever payment to {t.merchant}")

    usual = [h.amount for h in history if h.reference != t.reference]
    if usual and t.amount > 3 * Decimal(median(usual)):
        add("unusual_amount", 10, 0, f"{t.amount:,.0f} is far above the usual {median(usual):,.0f}")

    burst = [
        h
        for h in history
        if h.reference != t.reference
        and h.merchant == t.merchant
        and abs((h.occurred_at - t.occurred_at).total_seconds()) <= 3600
    ]
    if burst and t.device_known is False:
        add("burst", 15, 0, "several payments to the same new merchant within an hour")
    return out


def assess(evidence: Evidence, disputed_refs: Sequence[str]) -> DisputeAssessment:
    """Weigh the disputed debits. Unknown references are ignored (validation rejects them
    elsewhere); nothing disputed means 'unclear'."""
    debits = [t for t in evidence.transactions if t.kind == TransactionKind.DEBIT]
    disputed = [t for t in debits if t.reference in set(disputed_refs)]
    signals = [s for t in disputed for s in _signals(t, debits)]
    unauthorized = sum(s.points_unauthorized for s in signals)
    customer = sum(s.points_customer for s in signals)
    if not disputed:
        verdict: Verdict = "unclear"
        summary = "No disputed payment identified in the customer's records."
    elif unauthorized >= customer + MARGIN:
        verdict = "likely_unauthorized"
        summary = "The disputed payments do not match how this customer pays: likely fraud."
    elif customer >= unauthorized + MARGIN:
        verdict = "likely_customer_made"
        summary = (
            "The disputed payments match how this customer pays (own device, OTP-verified, "
            "a merchant they use): a specialist should review before any freeze or refund."
        )
    else:
        verdict = "unclear"
        summary = "The evidence is mixed: a specialist should review."
    return DisputeAssessment(
        verdict=verdict,
        disputed=[t.reference for t in disputed],
        unauthorized_score=unauthorized,
        customer_score=customer,
        signals=signals,
        summary=summary,
    )
