import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentflow.agents.triage import Category, TriageResult, Urgency
from agentflow.knowledge.schemas import KnowledgeAnswer
from agentflow.workflow.models import StepStatus, WorkflowStatus


class CreateWorkflowRequest(BaseModel):
    """A customer request entering the system (from a support inbox, chat, or the dashboard)."""

    message: str = Field(min_length=1, max_length=8000)
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


class WorkflowResult(BaseModel):
    """What the agents concluded. Grows a field per phase (resolution, actions, ...)."""

    triage: TriageResult | None = None
    knowledge: KnowledgeAnswer | None = None  # policy answer with citations (RAG)


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
    message_preview: str
    category: Category | None
    urgency: Urgency | None
    needs_human_review: bool | None
    created_at: datetime
