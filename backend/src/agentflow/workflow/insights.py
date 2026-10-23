"""Impact numbers for the dashboard: how much the agents resolved alone, money returned,
speed and AI cost. Computed on read from the last 30 days of workflows.

ponytail: aggregates in Python over at most 5,000 workflows; a materialized view or a
metrics store when an org outgrows that.
"""

import statistics
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.db import SessionDep
from agentflow.identity import service as identity
from agentflow.identity.deps import OrgContextDep
from agentflow.identity.schemas import BusinessProfile, Industry
from agentflow.tickets.models import Ticket, TicketStatus
from agentflow.tools.models import ActionExecution
from agentflow.workflow.models import Workflow, WorkflowStatus, WorkflowStep

router = APIRouter(prefix="/v1/insights", tags=["insights"])

# USD per million tokens (input, output), Groq list prices. Unknown models count as 0.
PRICES = {
    "openai/gpt-oss-20b": (Decimal("0.075"), Decimal("0.30")),
    "openai/gpt-oss-120b": (Decimal("0.15"), Decimal("0.60")),
}


class Insights(BaseModel):
    days: int
    requests: int
    completed: int
    resolved_by_ai: int  # completed with no approval and no ticket: nobody had to step in
    ai_resolution_rate: float  # resolved_by_ai / completed (0..1)
    waiting_approval: int
    open_tickets: int
    money_returned: Decimal  # refunds and reversals the tools executed (PKR)
    cards_frozen: int
    median_seconds_to_resolve: float | None  # for AI-resolved requests
    ai_cost_usd: Decimal
    ai_cost_per_request_usd: Decimal
    by_category: dict[str, int]
    by_channel: dict[str, int]
    # The business case, from the company's own numbers (business profile, or defaults).
    team_hours_saved: float  # requests the AI resolved alone x minutes a person would spend
    team_cost_saved: Decimal  # ... x the cost of an agent hour, in `currency`
    currency: str
    monthly_queries: int  # what the company told us it gets
    projected_monthly_hours_saved: float  # at that volume and today's AI resolution rate
    projected_monthly_cost_saved: Decimal
    assumptions: str  # shown next to the numbers: no hidden maths


async def compute(session: AsyncSession, org_id: uuid.UUID, days: int = 30) -> Insights:
    since = datetime.now(UTC) - timedelta(days=days)
    workflows = list(
        await session.scalars(
            select(Workflow)
            .where(Workflow.org_id == org_id, Workflow.created_at >= since)
            .limit(5000)
        )
    )
    completed = [w for w in workflows if w.status == WorkflowStatus.COMPLETED]
    by_ai = [
        w
        for w in completed
        if not (w.result or {}).get("ticket") and not (w.result or {}).get("approval")
    ]
    durations = [(w.ended_at - w.created_at).total_seconds() for w in by_ai if w.ended_at]

    actions = list(
        await session.scalars(
            select(ActionExecution).where(
                ActionExecution.org_id == org_id, ActionExecution.created_at >= since
            )
        )
    )
    executed = [a for a in actions if a.result.get("status") == "executed"]
    money = sum(
        (Decimal(str(a.result["amount"])) for a in executed if a.tool == "refund_transaction"),
        Decimal("0"),
    )

    cost = Decimal("0")
    usage = await session.execute(
        select(
            WorkflowStep.model,
            func.sum(WorkflowStep.input_tokens),
            func.sum(WorkflowStep.output_tokens),
        )
        .where(
            WorkflowStep.org_id == org_id,
            WorkflowStep.created_at >= since,
            WorkflowStep.model.is_not(None),
        )
        .group_by(WorkflowStep.model)
    )
    for model, tokens_in, tokens_out in usage:
        # "groq@2:openai/gpt-oss-120b" is the same model, priced the same.
        base = (model or "").split(":", 1)[-1] if (model or "").startswith("groq@") else model
        price_in, price_out = PRICES.get(base or "", (Decimal("0"), Decimal("0")))
        cost += (Decimal(tokens_in or 0) * price_in + Decimal(tokens_out or 0) * price_out) / 10**6

    open_tickets = await session.scalar(
        select(func.count())
        .select_from(Ticket)
        .where(Ticket.org_id == org_id, Ticket.status != TicketStatus.RESOLVED)
    )
    profile = (await identity.get_profile(session, org_id)).profile or BusinessProfile(
        industry=Industry.COMMERCIAL_BANK
    )
    rate = len(by_ai) / len(completed) if completed else 0.0
    per_query_hours = profile.minutes_per_query / 60
    hours = len(by_ai) * per_query_hours
    monthly_hours = profile.monthly_queries * rate * per_query_hours
    hourly = Decimal(profile.agent_cost_per_hour)
    return Insights(
        team_hours_saved=round(hours, 1),
        team_cost_saved=(Decimal(str(hours)) * hourly).quantize(Decimal("1")),
        currency=profile.currency,
        monthly_queries=profile.monthly_queries,
        projected_monthly_hours_saved=round(monthly_hours, 0),
        projected_monthly_cost_saved=(Decimal(str(monthly_hours)) * hourly).quantize(Decimal("1")),
        assumptions=(
            f"{profile.minutes_per_query} min of a person's time per routine query at "
            f"{profile.currency} {profile.agent_cost_per_hour:,}/hour; "
            f"{profile.monthly_queries:,} queries a month"
        ),
        days=days,
        requests=len(workflows),
        completed=len(completed),
        resolved_by_ai=len(by_ai),
        ai_resolution_rate=round(rate, 3),
        waiting_approval=sum(w.status == WorkflowStatus.WAITING_APPROVAL for w in workflows),
        open_tickets=open_tickets or 0,
        money_returned=money,
        cards_frozen=sum(a.tool == "freeze_card" for a in executed),
        median_seconds_to_resolve=round(statistics.median(durations), 1) if durations else None,
        ai_cost_usd=cost.quantize(Decimal("0.0001")),
        ai_cost_per_request_usd=(cost / len(workflows)).quantize(Decimal("0.00001"))
        if workflows
        else Decimal("0"),
        by_category=dict(
            Counter(
                ((w.result or {}).get("triage") or {}).get("category") or "pending"
                for w in workflows
            ).most_common()
        ),
        by_channel=dict(Counter(w.input.get("channel", "web") for w in workflows).most_common()),
    )


@router.get("")
async def get_insights(ctx: OrgContextDep, session: SessionDep) -> Insights:
    """Impact over the last 30 days: AI resolution rate, money returned, speed, AI cost."""
    return await compute(session, ctx.org_id)
