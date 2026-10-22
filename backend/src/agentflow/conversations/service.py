"""Customer conversations and the orchestration between them.

    help chat  --customer message-->  workflow (agents, policy, tools)
        |                                   |
        |   after triage: a problem? -------+--> open an ISSUE thread, say so in the help
        |                                   |    chat, and report the outcome in the issue
        v                                   v
    answer (documents / own records)    issue: refund, reversal, frozen card, records
                                        answer, or a ticket for a human (status follows)

A customer message becomes an ordinary workflow (same agents, policy and tools as every
channel). The worker calls `after_triage` mid-run and `post_outcome` when the workflow
pauses, finishes or fails. Routing is decided by code from triage and the criticality
analysis, never by the reply model.
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.conversations.models import (
    ChatMessage,
    Conversation,
    ConversationKind,
    ConversationMode,
    IssueStatus,
    MessageRole,
)
from agentflow.policy import risk as risk_analysis
from agentflow.workflow import service as workflows
from agentflow.workflow.graph import records_of
from agentflow.workflow.models import Workflow
from agentflow.workflow.schemas import CreateWorkflowRequest

CONTEXT_MESSAGES = 6  # earlier turns given to the agents, so "it" and "that one" resolve
MAX_MESSAGE_CHARS = 2000

# Triage categories that are a problem to resolve, not a question: they get their own
# issue thread. general_inquiry, other and kyc_document stay in the help chat
# ("what documents do I need for a loan?" is a question; a real run routed it to an issue).
ISSUE_CATEGORIES = {
    "duplicate_charge",
    "failed_payment",
    "refund_request",
    "unauthorized_transaction",
    "card_or_account_blocked",
    "account_access",
}


class ConversationNotFoundError(Exception):
    pass


async def help_conversation(
    session: AsyncSession, org_id: uuid.UUID, customer_ref: str
) -> Conversation:
    """The customer's help chat (one per customer), created on first use."""
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.org_id == org_id,
            Conversation.customer_ref == customer_ref,
            Conversation.kind == ConversationKind.HELP,
        )
    )
    if conversation is None:
        conversation = Conversation(
            org_id=org_id, customer_ref=customer_ref, kind=ConversationKind.HELP
        )
        session.add(conversation)
        await session.flush()
    return conversation


async def list_for_customer(
    session: AsyncSession, org_id: uuid.UUID, customer_ref: str
) -> list[Conversation]:
    """Help chat first, then issues newest first."""
    help_chat = await help_conversation(session, org_id, customer_ref)
    issues = await session.scalars(
        select(Conversation)
        .where(
            Conversation.org_id == org_id,
            Conversation.customer_ref == customer_ref,
            Conversation.kind == ConversationKind.ISSUE,
        )
        .order_by(Conversation.created_at.desc())
        .limit(50)
    )
    return [help_chat, *issues]


async def open_issue(
    session: AsyncSession,
    org_id: uuid.UUID,
    customer_ref: str,
    text: str,
    *,
    request_id: str | None,
) -> Conversation:
    """The customer reports a problem directly: a new issue thread, agents on it at once."""
    issue = Conversation(org_id=org_id, customer_ref=customer_ref, kind=ConversationKind.ISSUE)
    session.add(issue)
    await session.flush()
    await post_customer_message(session, issue, text, request_id=request_id)
    return issue


async def get(
    session: AsyncSession,
    org_id: uuid.UUID,
    conversation_id: uuid.UUID,
    customer_ref: str | None = None,
) -> Conversation:
    """A conversation of this org (and of this customer, when given)."""
    query = select(Conversation).where(
        Conversation.id == conversation_id, Conversation.org_id == org_id
    )
    if customer_ref is not None:
        query = query.where(Conversation.customer_ref == customer_ref)
    conversation = await session.scalar(query)
    if conversation is None:
        raise ConversationNotFoundError
    return conversation


async def messages(session: AsyncSession, conversation_id: uuid.UUID) -> list[ChatMessage]:
    rows = await session.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    )
    return list(rows)


def _context(history: list[ChatMessage]) -> str | None:
    """Recent turns, so the agents resolve "it" and "that one"."""
    earlier = [m for m in history if m.body][-CONTEXT_MESSAGES:]
    return "\n".join(f"{m.role.value}: {m.body[:400]}" for m in earlier) or None


async def post_customer_message(
    session: AsyncSession,
    conversation: Conversation,
    text: str,
    *,
    request_id: str | None,
    card: dict[str, Any] | None = None,
) -> ChatMessage:
    """Save the message and, unless a human took over, queue the agents for it."""
    history = await messages(session, conversation.id)
    message_id = uuid.uuid4()
    workflow_id = None
    if conversation.mode == ConversationMode.AI:
        wf, _ = await workflows.create(
            session,
            org_id=conversation.org_id,
            user_id=None,
            idempotency_key=f"chat-{message_id}",
            body=CreateWorkflowRequest(
                message=text,
                context=_context(history),
                channel="chat",
                customer_ref=conversation.customer_ref,
            ),
            request_id=request_id,
        )
        workflow_id = wf.id
    # Inserted complete: chat messages are append-only (no UPDATE grant).
    message = ChatMessage(
        id=message_id,
        org_id=conversation.org_id,
        conversation_id=conversation.id,
        role=MessageRole.CUSTOMER,
        body=text,
        card=card,
        workflow_id=workflow_id,
    )
    session.add(message)
    await session.flush()
    if conversation.kind == ConversationKind.ISSUE and conversation.status == IssueStatus.RESOLVED:
        conversation.status = IssueStatus.OPEN  # the customer says it isn't over
    conversation.updated_at = func.now()
    return message


async def post_agent_message(
    session: AsyncSession, conversation: Conversation, user_id: uuid.UUID, text: str
) -> ChatMessage:
    """A staff member writes: they own the conversation until they hand it back."""
    conversation.mode = ConversationMode.HUMAN
    message = ChatMessage(
        org_id=conversation.org_id,
        conversation_id=conversation.id,
        role=MessageRole.AGENT,
        body=text,
        author_id=user_id,
    )
    session.add(message)
    await session.flush()
    return message


async def post_team_update(
    session: AsyncSession,
    conversation: Conversation,
    *,
    user_id: uuid.UUID,
    text: str,
    card: dict[str, Any] | None = None,
) -> ChatMessage:
    """A message from the team that doesn't take the chat over (e.g. "your case is
    resolved"): the agents keep answering the customer's next messages."""
    message = ChatMessage(
        org_id=conversation.org_id,
        conversation_id=conversation.id,
        role=MessageRole.AGENT,
        body=text,
        card=card,
        author_id=user_id,
    )
    session.add(message)
    conversation.updated_at = func.now()
    await session.flush()
    return message


async def issue_for_ticket(session: AsyncSession, ticket_id: uuid.UUID) -> Conversation | None:
    return await session.scalar(select(Conversation).where(Conversation.ticket_id == ticket_id))


async def ticket_resolved(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    ticket_id: uuid.UUID,
    customer_ref: str,
    user_id: uuid.UUID,
    customer_message: str | None,
    card: dict[str, Any],
) -> None:
    """A specialist closed the ticket: close its issue, and tell the customer there (or in
    the help chat for tickets that came from another channel)."""
    issue = await issue_for_ticket(session, ticket_id)
    if issue is not None:
        issue.status = IssueStatus.RESOLVED
    if customer_message:
        target = issue or await help_conversation(session, org_id, customer_ref)
        await post_team_update(session, target, user_id=user_id, text=customer_message, card=card)


# --- Orchestration ---------------------------------------------------------------------------


def is_issue(triage: dict[str, Any], message: str) -> bool:
    """A problem to resolve (its own thread), not a question. Code decides, from triage
    and the criticality analysis: "I gave a caller my OTP" is an issue whatever the
    category."""
    if triage.get("category") in ISSUE_CATEGORIES:
        return True
    return risk_analysis.assess(
        message=message,
        triage_urgency=triage.get("urgency"),
        category=triage.get("category"),
        amount_at_stake=None,
        recent_requests=0,
    ).critical


async def _source(session: AsyncSession, workflow_id: uuid.UUID) -> ChatMessage | None:
    return await session.scalar(
        select(ChatMessage).where(
            ChatMessage.workflow_id == workflow_id, ChatMessage.role == MessageRole.CUSTOMER
        )
    )


async def _issue_from(session: AsyncSession, workflow_id: uuid.UUID) -> Conversation | None:
    return await session.scalar(
        select(Conversation).where(Conversation.origin_workflow_id == workflow_id)
    )


async def after_triage(
    session: AsyncSession, workflow_id: uuid.UUID, triage: dict[str, Any]
) -> None:
    """Called by the worker as soon as triage finishes (the rest of the run takes a while).

    From the help chat, a problem moves to a new issue thread: the customer sees that at
    once, and the outcome lands in the issue. Idempotent: a retried workflow finds the
    issue it already opened. In an issue thread, triage only names the issue."""
    source = await _source(session, workflow_id)
    if source is None:
        return  # not a chat workflow
    conversation = await session.get(Conversation, source.conversation_id)
    assert conversation is not None  # noqa: S101 (FK)
    # The customer-facing title; the summary is written for staff ("The customer reports").
    summary = (triage.get("title") or triage.get("summary") or source.body)[:300]
    if conversation.kind == ConversationKind.ISSUE:
        conversation.subject = conversation.subject or summary
        return
    if not is_issue(triage, source.body) or await _issue_from(session, workflow_id):
        return
    issue = Conversation(
        org_id=conversation.org_id,
        customer_ref=conversation.customer_ref,
        kind=ConversationKind.ISSUE,
        subject=summary,
        origin_workflow_id=workflow_id,
    )
    session.add(issue)
    await session.flush()
    session.add_all(
        [
            ChatMessage(  # the issue starts with what the customer said
                org_id=issue.org_id,
                conversation_id=issue.id,
                role=MessageRole.CUSTOMER,
                body=source.body,
                card=source.card,
            ),
            ChatMessage(
                org_id=conversation.org_id,
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                body="That sounds like a problem to fix, so I've opened a separate issue for "
                "it and I'm on it now. You'll see every update there.",
                card={"issue": {"id": str(issue.id), "subject": summary}},
                workflow_id=workflow_id,
            ),
        ]
    )


def outcome_card(state: dict[str, Any]) -> dict[str, Any]:
    """What the chat shows besides the text: facts from the workflow, never model prose."""
    card: dict[str, Any] = {}
    execution = state.get("execution") or {}
    proposal = state.get("proposal") or {}
    category = (state.get("triage") or {}).get("category")
    transactions = (state.get("evidence") or {}).get("transactions") or []

    if execution.get("status") == "executed" and execution.get("refund_reference"):
        card["refund"] = {
            "amount": execution.get("amount"),
            "currency": execution.get("currency"),
            "reference": execution.get("refund_reference"),
        }
    if execution.get("status") == "executed" and proposal.get("action") == "freeze_card":
        card["card_frozen"] = True
    if (approval := state.get("approval")) and approval.get("status") == "pending":
        card["review"] = {"amount": proposal.get("amount")}
    if ticket := state.get("ticket"):
        card["case"] = {"code": ticket.get("code"), "priority": ticket.get("priority")}
    if category == "failed_payment" and proposal.get("action") != "answer_from_records":
        # The transfer in question: the one acted on, else (an escalation) the one not
        # delivered. A records answer shows its own records instead.
        transfers = [t for t in transactions if t.get("transfer_status")]
        target = next(
            (t for t in transfers if t["reference"] == proposal.get("transaction_reference")),
            next((t for t in transfers if t["transfer_status"] != "credited"), None),
        )
        if target:
            card["transfer"] = {
                "to": target["merchant"],
                "amount": target["amount"],
                "currency": target["currency"],
                "reference": target["reference"],
                "network_status": target["transfer_status"],
                "sent_at": target["occurred_at"],
                "reversed": bool(card.get("refund")),
            }
    if records := records_of(state):
        card["records"] = records
    knowledge = state.get("knowledge") or {}
    if knowledge.get("status") == "answered":
        titles = dict.fromkeys(c["document_title"] for c in knowledge.get("citations") or [])
        if titles:
            card["sources"] = list(titles)
    return card


def _review_text(state: dict[str, Any]) -> str:
    amount = (state.get("proposal") or {}).get("amount")
    what = f"a refund of PKR {Decimal(str(amount)):,.0f}" if amount else "this action"
    return (
        f"I've checked your account and {what} looks right. Amounts like this need a "
        "second officer's sign-off, so it's with our team now. You'll get an update right "
        "here, no need to call."
    )


FAILED_TEXT = (
    "Sorry, something went wrong on our side while handling this. Your message is saved and "
    "a specialist will reply here."
)


def _failed_text(state: dict[str, Any]) -> str:
    code = (state.get("ticket") or {}).get("code")
    return FAILED_TEXT + (f" Your case number is {code}." if code else "")


def _issue_status(state: dict[str, Any], kind: str) -> IssueStatus:
    if kind == "failed" or state.get("ticket"):
        return IssueStatus.WITH_SPECIALIST
    if kind == "review":
        return IssueStatus.IN_REVIEW
    return IssueStatus.RESOLVED  # executed, answered from records, or nothing to do


async def post_outcome(
    session: AsyncSession, wf: Workflow, state: dict[str, Any], kind: str
) -> None:
    """Called by the worker when a chat workflow pauses ('review'), finishes ('reply') or
    gives up ('failed'). Posts into the issue the orchestrator opened for it, else where the
    customer wrote. No-op for workflows from other channels."""
    source = await _source(session, wf.id)
    if source is None:
        return
    target = await _issue_from(session, wf.id) or await session.get(
        Conversation, source.conversation_id
    )
    assert target is not None  # noqa: S101 (FK)
    body = {
        "review": _review_text(state),
        "reply": state.get("reply") or FAILED_TEXT,
        "failed": _failed_text(state),
    }[kind]
    session.add(
        ChatMessage(
            org_id=wf.org_id,
            conversation_id=target.id,
            role=MessageRole.ASSISTANT,
            body=body,
            card=outcome_card(state) or None,
            workflow_id=wf.id,
        )
    )
    target.updated_at = func.now()
    if target.kind == ConversationKind.ISSUE:
        target.status = _issue_status(state, kind)
        if ticket := state.get("ticket"):
            target.ticket_id = uuid.UUID(str(ticket["id"]))
