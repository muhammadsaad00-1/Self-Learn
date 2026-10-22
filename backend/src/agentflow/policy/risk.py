"""Criticality analysis: is this a case a human must own, and how urgently?

The triage model's urgency is one input. Code adds signals the model can't be talked out of
(a message saying "this is low priority" changes nothing here): account-takeover words, money
at stake, repeat contacts, legal threats, distress. The result can only *raise* priority,
never lower what triage said: a false alarm costs a minute of someone's time, a missed
account takeover costs the customer's savings.

Every signal is named, so the ticket and the decision trail say exactly why.
"""

import re
from decimal import Decimal
from typing import Literal, cast

from pydantic import BaseModel

Priority = Literal["low", "normal", "high", "critical"]
_RANK: dict[str, int] = {"low": 0, "normal": 1, "high": 2, "critical": 3}

HIGH_VALUE = Decimal("50000")  # PKR at stake that alone makes a case high priority
REPEAT_CONTACTS = 2  # earlier requests from the same customer in 72h

_GAVE = r"(shared?|gave|told|sent|bata\w*|de di\w*|diya)"
_CREDENTIAL = r"(otp|pin|cvv|password|code)"

# (signal id, points, pattern). English and Roman Urdu; case-insensitive.
_PATTERNS: list[tuple[str, int, str]] = [
    # The classic scam: a "bank officer" calls and asks for the OTP.
    (
        "credentials_shared",
        70,
        rf"\b{_GAVE}\b.{{0,40}}\b{_CREDENTIAL}\b|\b{_CREDENTIAL}\b.{{0,40}}\b{_GAVE}\b",
    ),
    (
        "account_takeover",
        70,
        r"\b(hack(ed)?|sim swap|someone (logged|changed)|account (taken|stolen)"
        r"|mera account (hack|chori))\b",
    ),
    ("scam", 40, r"\b(scam|fraud|dhok[ae]|thag[ia]?|stolen|chori)\b"),
    (
        "legal_threat",
        40,
        r"\b(court|lawyer|legal action|police|fir|sbp complaint|state bank|mohtasib|ombudsman)\b",
    ),
    ("distress", 40, r"\b(emergency|hospital|medicine|fees due|rent due|suicide|desperate)\b"),
]
_COMPILED = [(name, pts, re.compile(p, re.IGNORECASE)) for name, pts, p in _PATTERNS]


class RiskSignal(BaseModel):
    id: str
    points: int
    detail: str


class RiskAssessment(BaseModel):
    score: int  # 0..100
    priority: Priority  # max(triage urgency, score band)
    critical: bool
    signals: list[RiskSignal]


def _band(score: int) -> Priority:
    return "critical" if score >= 70 else "high" if score >= 40 else "normal"


def assess(
    *,
    message: str,
    triage_urgency: str | None,
    category: str | None,
    amount_at_stake: Decimal | None,
    recent_requests: int,
) -> RiskAssessment:
    signals: list[RiskSignal] = []
    for name, points, pattern in _COMPILED:
        if match := pattern.search(message):
            signals.append(RiskSignal(id=name, points=points, detail=f'"{match.group(0)}"'))
    if category == "unauthorized_transaction":
        signals.append(RiskSignal(id="fraud_category", points=40, detail="triaged as fraud"))
    if amount_at_stake is not None and amount_at_stake >= HIGH_VALUE:
        signals.append(
            RiskSignal(id="high_value", points=40, detail=f"PKR {amount_at_stake:,.0f} at stake")
        )
    if recent_requests >= REPEAT_CONTACTS:
        signals.append(
            RiskSignal(
                id="repeat_contact",
                points=30,
                detail=f"{recent_requests} earlier requests in 72 hours",
            )
        )
    if triage_urgency == "critical":
        signals.append(RiskSignal(id="triage_critical", points=70, detail="triage: critical"))

    score = min(100, sum(s.points for s in signals))
    banded = _band(score)
    triaged = triage_urgency if triage_urgency in _RANK else "normal"
    priority = cast(Priority, banded if _RANK[banded] >= _RANK[triaged] else triaged)
    return RiskAssessment(
        score=score, priority=priority, critical=priority == "critical", signals=signals
    )
