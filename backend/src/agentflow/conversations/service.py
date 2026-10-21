"""Chat use cases. A customer message becomes a workflow (the same agents, policy and
tools as every other channel); when the workflow pauses, finishes or fails, the worker posts
the outcome back into the chat with `post_outcome`."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.conversations.models import ChatMessage, Conversation, ConversationMode, MessageRole
from agentflow.workflow import service as workflows
from agentflow.workflow.models import Workflow
from agentflow.workflow.schemas import CreateWorkflowRequest

CONTEXT_MESSAGES = 6  # earlier turns given to the agents, so "it" and "that one" resolve
MAX_MESSAGE_CHARS = 2000


class ConversationNotFoundError(Exception):
    pass


async def open_for_customer(
    session: AsyncSession, org_id: uuid.UUID, customer_ref: str
) -> Conversation:
    """The customer's conversation (one per customer), created on first use."""
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.org_id == org_id, Conversation.customer_ref == customer_ref
        )
    )
    if conversation is None:
        conversation = Conversation(org_id=org_id, customer_ref=customer_ref)
        session.add(conversation)
        await session.flush()
    return conversation


async def get(session: AsyncSession, org_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.org_id == org_id
        )
    )
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


def _with_context(history: list[ChatMessage], text: str) -> str:
    """The workflow input: the new message, plus recent turns as context."""
    earlier = [m for m in history if m.body][-CONTEXT_MESSAGES:]
    if not earlier:
        return text
    lines = [f"{m.role.value}: {m.body[:400]}" for m in earlier]
    return (
        "Earlier in this chat (context only):\n"
        + "\n".join(lines)
        + f"\n\nCustomer's new message:\n{text}"
    )


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
                message=_with_context(history, text),
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
    if category == "failed_payment":
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


async def post_outcome(
    session: AsyncSession, wf: Workflow, state: dict[str, Any], kind: str
) -> None:
    """Called by the worker when a chat workflow pauses ('review'), finishes ('reply') or
    gives up ('failed'). No-op for workflows from other channels."""
    source = await session.scalar(
        select(ChatMessage).where(
            ChatMessage.workflow_id == wf.id, ChatMessage.role == MessageRole.CUSTOMER
        )
    )
    if source is None:
        return
    body = {
        "review": _review_text(state),
        "reply": state.get("reply") or FAILED_TEXT,
        "failed": FAILED_TEXT,
    }[kind]
    session.add(
        ChatMessage(
            org_id=wf.org_id,
            conversation_id=source.conversation_id,
            role=MessageRole.ASSISTANT,
            body=body,
            card=outcome_card(state) or None,
            workflow_id=wf.id,
        )
    )
