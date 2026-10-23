import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentflow.agents.resolution import ProposedAction
from agentflow.agents.triage import Category, TriageResult, Urgency
from agentflow.knowledge.schemas import KnowledgeAnswer
from agentflow.ledger.schemas import Evidence
from agentflow.policy.dispute import DisputeAssessment
from agentflow.policy.engine import PolicyDecision
from agentflow.policy.risk import RiskAssessment
from agentflow.tools.refund import ExecutionResult
from agentflow.tools.tickets import TicketRef
from agentflow.validation import non_blank
from agentflow.workflow.models import StepStatus, WorkflowStatus


class CreateWorkflowRequest(BaseModel):
    """A customer request entering the system (from a support inbox, chat, or the dashboard)."""

    message: Annotated[str, non_blank(8000)]
    # Earlier turns of a conversation, for the agents only: never shown as the customer's
    # message (the dashboard and tickets show `message`, what the customer actually wrote).
    context: str | None = Field(default=None, max_length=6000)
    channel: Literal["web", "email", "chat", "phone"] = "web"
    # The business's own customer id, if known. Never a card or account number.
    customer_ref: str | None = Field(default=None, max_length=100)


class WorkflowStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node: str
    attempt: int
    status: StepStatus
    output: dict[str, Any] | None
    error: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    created_at: datetime


class ApprovalRef(BaseModel):
    id: uuid.UUID
    status: str


class WorkflowResult(BaseModel):
    """What the agents concluded. Grows a field per phase (resolution, actions, ...)."""

    triage: TriageResult | None = None
    knowledge: KnowledgeAnswer | None = None  # policy answer with citations (RAG)
    evidence: Evidence | None = None  # ledger research
    proposal: ProposedAction | None = None  # resolution agent, validated
    decision: PolicyDecision | None = None  # policy engine
    risk: RiskAssessment | None = None  # criticality analysis (code)
    dispute: DisputeAssessment | None = None  # fraud claims: what the records say (code)
    approval: ApprovalRef | None = None  # human decision, if one was needed
    execution: ExecutionResult | None = None  # what the tool actually did
    ticket: TicketRef | None = None  # opened when a human must own the case
    reply: str | None = None  # drafted customer reply


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: WorkflowStatus
    input: dict[str, Any]
    result: WorkflowResult | None
    error: str | None
    attempts: int
    request_id: str | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    steps: list[WorkflowStepOut] = []


class WorkflowSummary(BaseModel):
    """One row in the workflow list: the triage headline without the step details."""

    id: uuid.UUID
    status: WorkflowStatus
    message_preview: str  # what the customer wrote (never the agents' context)
    customer_ref: str | None
    channel: str
    category: Category | None
    urgency: Urgency | None
    needs_human_review: bool | None
    created_at: datetime
