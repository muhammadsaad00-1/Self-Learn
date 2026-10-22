"""Customer chat API.

/v1/customer       customer login (phone + one-time code) and their own dashboard data
/v1/chat           the customer's side, authenticated by a customer token (one conversation)
/v1/conversations  the team's side: open a customer's chat, read every conversation, take
                   over from the agents and hand back
"""

import hashlib
import hmac
import re
import secrets
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

import groq
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on

from agentflow.audit import service as audit
from agentflow.config import Settings
from agentflow.conversations import service
from agentflow.conversations.models import (
    ChatMessage,
    Conversation,
    ConversationMode,
    MessageRole,
)
from agentflow.db import SessionDep, set_tenant_context
from agentflow.identity.deps import OrgContextDep, SettingsDep
from agentflow.identity.security import create_customer_token, decode_customer_token
from agentflow.ledger import service as ledger
from agentflow.ledger.models import Account, CardStatus
from agentflow.ledger.schemas import TransactionOut
from agentflow.observability import request_id_var
from agentflow.tickets import service as tickets
from agentflow.tickets.models import TicketStatus

router = APIRouter(prefix="/v1/chat", tags=["chat"])
staff_router = APIRouter(prefix="/v1/conversations", tags=["conversations"])
customer_router = APIRouter(prefix="/v1/customer", tags=["customer"])

# (audio bytes, filename) -> text. Injectable: tests set app.state.transcriber.
Transcriber = Callable[[bytes, str], Awaitable[str]]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    body: str
    card: dict[str, Any] | None
    created_at: datetime


class ChatOut(BaseModel):
    customer_name: str | None
    mode: ConversationMode
    thinking: bool  # the agents are working on the customer's last message
    messages: list[MessageOut]


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=service.MAX_MESSAGE_CHARS)


class OpenChatIn(BaseModel):
    customer_ref: str = Field(min_length=1, max_length=100)


class ConversationOut(BaseModel):
    id: uuid.UUID
    customer_ref: str
    customer_name: str | None
    mode: ConversationMode
    last_message: MessageOut | None
    updated_at: datetime


class OpenedChat(BaseModel):
    conversation: ConversationOut
    customer_token: str  # give to the customer's app; valid customer_token_ttl_hours


async def _name(session: SessionDep, c: Conversation) -> str | None:
    return await session.scalar(
        select(Account.customer_name).where(
            Account.org_id == c.org_id, Account.customer_ref == c.customer_ref
        )
    )


async def _chat(session: SessionDep, c: Conversation) -> ChatOut:
    msgs = await service.messages(session, c.id)
    last = msgs[-1] if msgs else None
    return ChatOut(
        customer_name=await _name(session, c),
        mode=c.mode,
        thinking=bool(
            last
            and last.role == MessageRole.CUSTOMER
            and last.workflow_id
            and c.mode == ConversationMode.AI
        ),
        messages=[MessageOut.model_validate(m) for m in msgs],
    )


# --- Customer side --------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False, description="Customer token from POST /v1/conversations")


async def customer_conversation(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: SessionDep,
    settings: SettingsDep,
) -> Conversation:
    claims = decode_customer_token(creds.credentials, settings) if creds else None
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired chat link")
    org_id, conversation_id = claims
    await set_tenant_context(session, org_id=org_id)
    try:
        return await service.get(session, org_id, conversation_id)
    except service.ConversationNotFoundError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired chat link") from None


CustomerConversation = Annotated[Conversation, Depends(customer_conversation)]


async def _rate_limit(request: Request, conversation: Conversation, settings: Settings) -> None:
    """Each message runs several model calls: cap it per conversation (fixed window)."""
    redis = request.app.state.redis
    key = f"chat:{conversation.id}"
    await redis.set(key, 0, ex=60, nx=True)
    if await redis.incr(key) > settings.chat_messages_per_minute:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You're sending messages too fast, try again in a minute",
            headers={"Retry-After": str(await redis.ttl(key))},
        )


@router.get("")
async def get_chat(conversation: CustomerConversation, session: SessionDep) -> ChatOut:
    """The whole conversation. Poll it while `thinking` is true."""
    return await _chat(session, conversation)


@router.post("/messages", status_code=status.HTTP_201_CREATED)
async def send_message(
    body: TextIn,
    request: Request,
    conversation: CustomerConversation,
    session: SessionDep,
    settings: SettingsDep,
) -> MessageOut:
    await _rate_limit(request, conversation, settings)
    message = await service.post_customer_message(
        session, conversation, body.text.strip(), request_id=request_id_var.get()
    )
    await session.flush()
    await session.refresh(message)
    return MessageOut.model_validate(message)


def get_transcriber(request: Request) -> Transcriber:
    if request.app.state.transcriber is None:
        settings: Settings = request.app.state.settings
        if settings.groq_api_key is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Voice notes are off")
        client = groq.AsyncGroq(api_key=settings.groq_api_key.get_secret_value())

        async def transcribe(data: bytes, filename: str) -> str:
            result = await client.audio.transcriptions.create(
                file=(filename, data),
                model=settings.transcription_model,
                # A hint, not an instruction: biases spelling towards banking words.
                prompt="Bank customer support voice note: transfer, IBFT, RAAST, cheque, card.",
            )
            return result.text.strip()

        request.app.state.transcriber = transcribe
    transcriber: Transcriber = request.app.state.transcriber
    return transcriber


@router.post("/voice", status_code=status.HTTP_201_CREATED)
async def send_voice_note(
    request: Request,
    conversation: CustomerConversation,
    session: SessionDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="Recorded audio (webm/ogg/m4a/mp3/wav)")],
) -> MessageOut:
    """Voice note -> Whisper transcript -> the same pipeline as a typed message."""
    await _rate_limit(request, conversation, settings)
    data = await file.read(settings.max_voice_bytes + 1)
    if len(data) > settings.max_voice_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Voice note is too long")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Voice note is empty")
    text = (await get_transcriber(request)(data, file.filename or "voice.webm"))[
        : service.MAX_MESSAGE_CHARS
    ]
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Couldn't hear anything")
    message = await service.post_customer_message(
        session, conversation, text, request_id=request_id_var.get(), card={"voice": True}
    )
    await session.flush()
    await session.refresh(message)
    return MessageOut.model_validate(message)


# --- Team side ------------------------------------------------------------------------------


async def _summary(
    session: SessionDep, c: Conversation, last: ChatMessage | None
) -> ConversationOut:
    return ConversationOut(
        id=c.id,
        customer_ref=c.customer_ref,
        customer_name=await _name(session, c),
        mode=c.mode,
        last_message=MessageOut.model_validate(last) if last else None,
        updated_at=c.updated_at,
    )


@staff_router.post("")
async def open_chat(
    body: OpenChatIn, ctx: OrgContextDep, session: SessionDep, settings: SettingsDep
) -> OpenedChat:
    """Open (or reopen) a customer's chat and issue a customer token for it. In production
    the bank's app does this after its own customer login; here staff use it for demos."""
    try:
        await ledger.get_account(session, ctx.org_id, body.customer_ref)
    except ledger.AccountNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found") from None
    conversation = await service.open_for_customer(session, ctx.org_id, body.customer_ref)
    await audit.record(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="conversation.link_issued",
        target_type="conversation",
        target_id=conversation.id,
        details={"customer_ref": body.customer_ref},
    )
    await session.flush()
    await session.refresh(conversation)
    msgs = await service.messages(session, conversation.id)
    return OpenedChat(
        conversation=await _summary(session, conversation, msgs[-1] if msgs else None),
        customer_token=create_customer_token(ctx.org_id, conversation.id, settings),
    )


@staff_router.get("")
async def list_conversations(ctx: OrgContextDep, session: SessionDep) -> list[ConversationOut]:
    """The 50 most recently active conversations, newest first."""
    conversations = list(
        await session.scalars(
            select(Conversation)
            .where(Conversation.org_id == ctx.org_id)
            .order_by(Conversation.updated_at.desc())
            .limit(50)
        )
    )
    lasts = {
        m.conversation_id: m
        for m in await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id.in_([c.id for c in conversations]))
            .ext(distinct_on(ChatMessage.conversation_id))
            .order_by(ChatMessage.conversation_id, ChatMessage.created_at.desc())
        )
    }
    return [await _summary(session, c, lasts.get(c.id)) for c in conversations]


async def _get(session: SessionDep, ctx: OrgContextDep, conversation_id: uuid.UUID) -> Conversation:
    try:
        return await service.get(session, ctx.org_id, conversation_id)
    except service.ConversationNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found") from None


@staff_router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID, ctx: OrgContextDep, session: SessionDep
) -> ChatOut:
    return await _chat(session, await _get(session, ctx, conversation_id))


@staff_router.post("/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
async def reply_as_agent(
    conversation_id: uuid.UUID, body: TextIn, ctx: OrgContextDep, session: SessionDep
) -> MessageOut:
    """Write to the customer as a person. The agents stop answering until handed back."""
    conversation = await _get(session, ctx, conversation_id)
    message = await service.post_agent_message(session, conversation, ctx.user.id, body.text)
    await audit.record(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="conversation.agent_replied",
        target_type="conversation",
        target_id=conversation.id,
    )
    await session.flush()
    await session.refresh(message)
    return MessageOut.model_validate(message)


@staff_router.post("/{conversation_id}/handback")
async def hand_back_to_agents(
    conversation_id: uuid.UUID, ctx: OrgContextDep, session: SessionDep
) -> ChatOut:
    conversation = await _get(session, ctx, conversation_id)
    conversation.mode = ConversationMode.AI
    await audit.record(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="conversation.handed_back",
        target_type="conversation",
        target_id=conversation.id,
    )
    await session.flush()
    return await _chat(session, conversation)


# --- Customer login and dashboard -------------------------------------------------------------
#
# ponytail: one-time codes are returned in the response outside production instead of being
# sent by SMS; plug an SMS provider into `login_start` for a real deployment.

OTP_TTL_SECONDS = 300
OTP_MAX_TRIES = 5
OTP_SENDS_PER_10_MIN = 3


class LoginStartIn(BaseModel):
    bank_id: uuid.UUID  # the bank's organization id, from the bank's customer-app link
    phone: str = Field(min_length=10, max_length=20)


class LoginStartOut(BaseModel):
    sent: bool  # always true: the answer never reveals whether the number is registered
    dev_code: str | None = None  # outside production only (no SMS provider yet)


class LoginVerifyIn(LoginStartIn):
    code: str = Field(pattern=r"^\d{6}$")


class CustomerSession(BaseModel):
    customer_token: str


class CustomerMe(BaseModel):
    customer_ref: str
    customer_name: str
    phone: str | None  # masked
    card_status: CardStatus


class CustomerCase(BaseModel):
    code: str
    status: TicketStatus
    category: str | None
    created_at: datetime
    resolved_at: datetime | None


def _normalize_phone(phone: str) -> str:
    """03001234567 / +92 300 1234567 / 923001234567 -> 923001234567."""
    digits = re.sub(r"\D", "", phone)
    return "92" + digits[1:] if digits.startswith("0") else digits


def _otp_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@customer_router.post("/login/start")
async def login_start(
    body: LoginStartIn, request: Request, session: SessionDep, settings: SettingsDep
) -> LoginStartOut:
    """Send a 6-digit one-time code to the customer's registered mobile number."""
    redis = request.app.state.redis
    phone = _normalize_phone(body.phone)
    sends = f"otp-sends:{body.bank_id}:{phone}"
    await redis.set(sends, 0, ex=600, nx=True)
    if await redis.incr(sends) > OTP_SENDS_PER_10_MIN:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many codes, try later")
    await set_tenant_context(session, org_id=body.bank_id)
    account = await ledger.get_account_by_phone(session, body.bank_id, phone)
    if account is None:
        return LoginStartOut(sent=True)  # same answer as success: no number enumeration
    code = f"{secrets.randbelow(10**6):06d}"
    await redis.set(f"otp:{body.bank_id}:{phone}", _otp_hash(code), ex=OTP_TTL_SECONDS)
    await redis.delete(f"otp-tries:{body.bank_id}:{phone}")
    return LoginStartOut(sent=True, dev_code=code if settings.env != "prod" else None)


@customer_router.post("/login/verify")
async def login_verify(
    body: LoginVerifyIn, request: Request, session: SessionDep, settings: SettingsDep
) -> CustomerSession:
    """Exchange the one-time code for a customer token (the customer's session)."""
    redis = request.app.state.redis
    phone = _normalize_phone(body.phone)
    key, tries = f"otp:{body.bank_id}:{phone}", f"otp-tries:{body.bank_id}:{phone}"
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong or expired code")
    expected = await redis.get(key)
    if expected is None:
        raise invalid
    await redis.set(tries, 0, ex=OTP_TTL_SECONDS, nx=True)
    if await redis.incr(tries) > OTP_MAX_TRIES:  # brute force: locked until a new code
        raise invalid
    if not hmac.compare_digest(expected.decode(), _otp_hash(body.code)):
        raise invalid
    await redis.delete(key, tries)  # one-time

    await set_tenant_context(session, org_id=body.bank_id)
    account = await ledger.get_account_by_phone(session, body.bank_id, phone)
    if account is None:
        raise invalid
    conversation = await service.open_for_customer(session, body.bank_id, account.customer_ref)
    await audit.record(
        session,
        org_id=body.bank_id,
        actor_user_id=None,
        action="customer.login",
        target_type="customer",
        target_id=account.customer_ref,
    )
    return CustomerSession(
        customer_token=create_customer_token(body.bank_id, conversation.id, settings)
    )


@customer_router.get("/me")
async def customer_me(conversation: CustomerConversation, session: SessionDep) -> CustomerMe:
    account = await ledger.get_account(session, conversation.org_id, conversation.customer_ref)
    return CustomerMe(
        customer_ref=account.customer_ref,
        customer_name=account.customer_name,
        phone=f"+{account.phone[:2]} *** ***{account.phone[-4:]}" if account.phone else None,
        card_status=account.card_status,
    )


@customer_router.get("/transactions")
async def customer_transactions(
    conversation: CustomerConversation, session: SessionDep
) -> list[TransactionOut]:
    """The customer's last 30 days, newest first: the same data the agents read."""
    account = await ledger.get_account(session, conversation.org_id, conversation.customer_ref)
    return await ledger.recent_transactions(session, account)


@customer_router.get("/cases")
async def customer_cases(
    conversation: CustomerConversation, session: SessionDep
) -> list[CustomerCase]:
    """The customer's support cases. Internal notes and titles stay with the team."""
    rows = await tickets.for_customer(session, conversation.org_id, conversation.customer_ref)
    return [
        CustomerCase(
            code=t.code,
            status=t.status,
            category=t.category,
            created_at=t.created_at,
            resolved_at=t.resolved_at,
        )
        for t in rows
    ]
