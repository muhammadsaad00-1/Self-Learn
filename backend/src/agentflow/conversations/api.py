"""Customer conversations API.

/v1/customer       the customer app: login (phone + one-time code), their help chat, their
                   issues, their records. Authenticated by a customer token.
/v1/conversations  the team: every conversation, take over from the agents, hand back, and
                   issue a customer token for demos.
"""

import hashlib
import hmac
import re
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

import groq
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.audit import service as audit
from agentflow.config import Settings
from agentflow.conversations import service
from agentflow.conversations.models import (
    ChatMessage,
    Conversation,
    ConversationKind,
    ConversationMode,
    IssueStatus,
    MessageRole,
)
from agentflow.db import SessionDep, set_tenant_context
from agentflow.identity import service as identity
from agentflow.identity.deps import OrgContextDep, SettingsDep
from agentflow.identity.security import create_customer_token, decode_customer_token
from agentflow.ledger import service as ledger
from agentflow.ledger.models import Account, CardStatus
from agentflow.ledger.schemas import TransactionOut
from agentflow.observability import request_id_var
from agentflow.tickets import service as tickets
from agentflow.validation import non_blank

customer_router = APIRouter(prefix="/v1/customer", tags=["customer"])
staff_router = APIRouter(prefix="/v1/conversations", tags=["conversations"])

# (audio bytes, filename) -> text. Injectable: tests set app.state.transcriber.
Transcriber = Callable[[bytes, str], Awaitable[str]]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    body: str
    card: dict[str, Any] | None
    created_at: datetime


class ConversationOut(BaseModel):
    id: uuid.UUID
    kind: ConversationKind
    customer_ref: str
    customer_name: str | None
    mode: ConversationMode
    status: IssueStatus  # meaningful for issues
    subject: str | None
    case_code: str | None  # the ticket, when a human owns the issue
    last_message: MessageOut | None
    created_at: datetime
    updated_at: datetime


class ChatOut(ConversationOut):
    thinking: bool  # the agents are working on the customer's last message
    messages: list[MessageOut]


class TextIn(BaseModel):
    text: Annotated[str, non_blank(service.MAX_MESSAGE_CHARS)]


async def _names(session: AsyncSession, org_id: uuid.UUID, refs: set[str]) -> dict[str, str]:
    rows = await session.execute(
        select(Account.customer_ref, Account.customer_name).where(
            Account.org_id == org_id, Account.customer_ref.in_(refs)
        )
    )
    return {ref: name for ref, name in rows}


async def _summaries(session: AsyncSession, convs: list[Conversation]) -> list[ConversationOut]:
    """Conversations with their last message, customer name and case code (4 queries)."""
    if not convs:
        return []
    lasts = {
        m.conversation_id: m
        for m in await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id.in_([c.id for c in convs]))
            .ext(distinct_on(ChatMessage.conversation_id))
            .order_by(ChatMessage.conversation_id, ChatMessage.created_at.desc())
        )
    }
    names = await _names(session, convs[0].org_id, {c.customer_ref for c in convs})
    codes = await tickets.codes(session, [c.ticket_id for c in convs if c.ticket_id])
    return [
        ConversationOut(
            id=c.id,
            kind=c.kind,
            customer_ref=c.customer_ref,
            customer_name=names.get(c.customer_ref),
            mode=c.mode,
            status=c.status,
            subject=c.subject,
            case_code=codes.get(c.ticket_id) if c.ticket_id else None,
            last_message=MessageOut.model_validate(lasts[c.id]) if c.id in lasts else None,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in convs
    ]


async def _chat(session: AsyncSession, c: Conversation) -> ChatOut:
    await session.flush()
    await session.refresh(c)  # server-set timestamps
    [summary] = await _summaries(session, [c])
    msgs = await service.messages(session, c.id)
    last = msgs[-1] if msgs else None
    return ChatOut(
        **summary.model_dump(),
        thinking=bool(
            last
            and last.role == MessageRole.CUSTOMER
            and last.workflow_id
            and c.mode == ConversationMode.AI
        ),
        messages=[MessageOut.model_validate(m) for m in msgs],
    )


# --- Customer side ------------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False, description="Customer token from the customer login")


@dataclass(frozen=True)
class Customer:
    org_id: uuid.UUID
    customer_ref: str


async def current_customer(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: SessionDep,
    settings: SettingsDep,
) -> Customer:
    claims = decode_customer_token(creds.credentials, settings) if creds else None
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in again")
    org_id, customer_ref = claims
    await set_tenant_context(session, org_id=org_id)
    try:
        await ledger.get_account(session, org_id, customer_ref)
    except ledger.AccountNotFoundError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in again") from None
    return Customer(org_id, customer_ref)


CustomerDep = Annotated[Customer, Depends(current_customer)]


async def _own(session: AsyncSession, me: Customer, conversation_id: uuid.UUID) -> Conversation:
    """One of *this customer's* conversations; anyone else's is simply not found."""
    try:
        return await service.get(session, me.org_id, conversation_id, me.customer_ref)
    except service.ConversationNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found") from None


async def _rate_limit(request: Request, me: Customer, settings: Settings) -> None:
    """Each message runs several model calls: cap it per customer (fixed window)."""
    redis = request.app.state.redis
    key = f"chat:{me.org_id}:{me.customer_ref}"
    await redis.set(key, 0, ex=60, nx=True)
    if await redis.incr(key) > settings.chat_messages_per_minute:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You're sending messages too fast, try again in a minute",
            headers={"Retry-After": str(await redis.ttl(key))},
        )


@customer_router.get("/conversations")
async def my_conversations(me: CustomerDep, session: SessionDep) -> list[ConversationOut]:
    """The help chat first, then my issues (newest first)."""
    return await _summaries(
        session, await service.list_for_customer(session, me.org_id, me.customer_ref)
    )


@customer_router.post("/issues", status_code=status.HTTP_201_CREATED)
async def report_issue(
    body: TextIn, request: Request, me: CustomerDep, session: SessionDep, settings: SettingsDep
) -> ChatOut:
    """Report a problem: a new issue thread, the agents start on it at once."""
    await _rate_limit(request, me, settings)
    issue = await service.open_issue(
        session, me.org_id, me.customer_ref, body.text.strip(), request_id=request_id_var.get()
    )
    return await _chat(session, issue)


@customer_router.get("/conversations/{conversation_id}")
async def my_conversation(
    conversation_id: uuid.UUID, me: CustomerDep, session: SessionDep
) -> ChatOut:
    """The whole conversation. Poll it while `thinking` is true."""
    return await _chat(session, await _own(session, me, conversation_id))


@customer_router.post("/conversations/{conversation_id}/messages", status_code=201)
async def send_message(
    conversation_id: uuid.UUID,
    body: TextIn,
    request: Request,
    me: CustomerDep,
    session: SessionDep,
    settings: SettingsDep,
) -> MessageOut:
    conversation = await _own(session, me, conversation_id)
    await _rate_limit(request, me, settings)
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


@customer_router.post("/conversations/{conversation_id}/voice", status_code=201)
async def send_voice_note(
    conversation_id: uuid.UUID,
    request: Request,
    me: CustomerDep,
    session: SessionDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="Recorded audio (webm/ogg/m4a/mp3/wav)")],
) -> MessageOut:
    """Voice note -> Whisper transcript -> the same pipeline as a typed message."""
    conversation = await _own(session, me, conversation_id)
    await _rate_limit(request, me, settings)
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


class CustomerMe(BaseModel):
    bank_name: str
    customer_ref: str
    customer_name: str
    phone: str | None  # masked
    card_status: CardStatus


@customer_router.get("/me")
async def customer_me(me: CustomerDep, session: SessionDep) -> CustomerMe:
    account = await ledger.get_account(session, me.org_id, me.customer_ref)
    return CustomerMe(
        bank_name=(await identity.get_profile(session, me.org_id)).name,
        customer_ref=account.customer_ref,
        customer_name=account.customer_name,
        phone=f"+{account.phone[:2]} *** ***{account.phone[-4:]}" if account.phone else None,
        card_status=account.card_status,
    )


@customer_router.get("/transactions")
async def customer_transactions(me: CustomerDep, session: SessionDep) -> list[TransactionOut]:
    """The customer's last 30 days, newest first: the same data the agents read."""
    account = await ledger.get_account(session, me.org_id, me.customer_ref)
    return await ledger.recent_transactions(session, account)


class Bank(BaseModel):
    name: str


@customer_router.get("/bank/{bank_id}")
async def bank(bank_id: uuid.UUID, session: SessionDep) -> Bank:
    """The bank's name for its customer-app login screen (the id comes from the bank's own
    app link; nothing else about the company is exposed)."""
    await set_tenant_context(session, org_id=bank_id)
    try:
        return Bank(name=(await identity.get_profile(session, bank_id)).name)
    except identity.OrgNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown bank") from None


# --- Customer login --------------------------------------------------------------------------
#
# ponytail: one-time codes are returned in the response outside production instead of being
# sent by SMS; plug an SMS provider into `login_start` for a real deployment.

OTP_TTL_SECONDS = 300
OTP_MAX_TRIES = 5
OTP_SENDS_PER_10_MIN = 3
LOOKUPS_PER_10_MIN = 10


def _normalize_phone(phone: str) -> str:
    """03001234567 / +92 300 1234567 / 923001234567 -> 923001234567."""
    digits = re.sub(r"\D", "", phone)
    return "92" + digits[1:] if digits.startswith("0") else digits


def _valid_phone(phone: str) -> str:
    """A mobile number has 11-13 digits once normalized; "abcdefghijk" is not one (QA)."""
    if not 11 <= len(_normalize_phone(phone)) <= 13:
        raise ValueError("Enter a mobile number like 0300 1234567")
    return phone


Phone = Annotated[str, Field(min_length=10, max_length=20), AfterValidator(_valid_phone)]


class LoginStartIn(BaseModel):
    phone: Phone
    username: Annotated[str, non_blank(64)]
    # Preselected by the bank's own app link, or chosen when the customer banks with several.
    bank_id: uuid.UUID | None = None


class BankOption(BaseModel):
    id: uuid.UUID
    name: str


class LoginStartOut(BaseModel):
    # True: if the details match a customer, a code was sent (unknown details look the same).
    # False: the customer banks with several companies and must choose one (`banks`).
    sent: bool
    bank: BankOption | None = None  # the bank the code is for
    banks: list[BankOption] = []
    dev_code: str | None = None  # local/test only (no SMS provider yet)


class LoginVerifyIn(BaseModel):
    bank_id: uuid.UUID  # from the start step
    phone: Phone
    code: str = Field(pattern=r"^\d{6}$")


class CustomerSession(BaseModel):
    customer_token: str


def _otp_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def _throttle(request: Request, key: str, limit: int, message: str) -> None:
    redis = request.app.state.redis
    await redis.set(key, 0, ex=600, nx=True)
    if await redis.incr(key) > limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, message)


@customer_router.post("/login/start")
async def login_start(
    body: LoginStartIn, request: Request, session: SessionDep, settings: SettingsDep
) -> LoginStartOut:
    """Phone + username identify the customer and their bank; a 6-digit one-time code goes
    to the registered mobile. Works with or without the bank's own link."""
    phone, username = _normalize_phone(body.phone), body.username.strip().lower()
    await _throttle(
        request, f"login-lookups:{phone}", LOOKUPS_PER_10_MIN, "Too many attempts, try later"
    )
    rows = await session.execute(
        text("SELECT bank_id, bank_name FROM customer_login_banks(:phone, :username)"),
        {"phone": phone, "username": username},
    )
    banks = [BankOption(id=i, name=n) for i, n in rows]
    if body.bank_id is not None:
        banks = [b for b in banks if b.id == body.bank_id]
    if not banks:
        return LoginStartOut(sent=True)  # same as success: no account enumeration
    if len(banks) > 1:
        return LoginStartOut(sent=False, banks=banks)  # they know both: let them choose
    bank = banks[0]
    await _throttle(
        request, f"otp-sends:{bank.id}:{phone}", OTP_SENDS_PER_10_MIN, "Too many codes, try later"
    )
    code = f"{secrets.randbelow(10**6):06d}"
    redis = request.app.state.redis
    await redis.set(f"otp:{bank.id}:{phone}", _otp_hash(code), ex=OTP_TTL_SECONDS)
    await redis.delete(f"otp-tries:{bank.id}:{phone}")
    return LoginStartOut(
        sent=True, bank=bank, dev_code=code if settings.env in ("local", "test") else None
    )


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
    await audit.record(
        session,
        org_id=body.bank_id,
        actor_user_id=None,
        action="customer.login",
        target_type="customer",
        target_id=account.customer_ref,
    )
    return CustomerSession(
        customer_token=create_customer_token(body.bank_id, account.customer_ref, settings)
    )


# --- Team side ------------------------------------------------------------------------------


class OpenChatIn(BaseModel):
    customer_ref: Annotated[str, non_blank(100)]


class OpenedChat(BaseModel):
    conversation: ConversationOut  # the customer's help chat
    customer_token: str  # opens the customer app as this customer (customer_token_ttl_hours)


@staff_router.post("")
async def open_customer_app(
    body: OpenChatIn, ctx: OrgContextDep, session: SessionDep, settings: SettingsDep
) -> OpenedChat:
    """Issue a customer token to open the customer app as this customer (demos and
    assisted support). In production customers sign in themselves (phone + code)."""
    try:
        await ledger.get_account(session, ctx.org_id, body.customer_ref)
    except ledger.AccountNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found") from None
    conversation = await service.help_conversation(session, ctx.org_id, body.customer_ref)
    await audit.record(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="conversation.link_issued",
        target_type="customer",
        target_id=body.customer_ref,
    )
    await session.flush()
    await session.refresh(conversation)
    [summary] = await _summaries(session, [conversation])
    return OpenedChat(
        conversation=summary,
        customer_token=create_customer_token(ctx.org_id, body.customer_ref, settings),
    )


@staff_router.get("")
async def list_conversations(ctx: OrgContextDep, session: SessionDep) -> list[ConversationOut]:
    """The 50 most recently active conversations (help chats and issues), newest first."""
    conversations = list(
        await session.scalars(
            select(Conversation)
            .where(Conversation.org_id == ctx.org_id)
            .order_by(Conversation.updated_at.desc())
            .limit(50)
        )
    )
    return await _summaries(session, conversations)


async def _get(
    session: AsyncSession, ctx: OrgContextDep, conversation_id: uuid.UUID
) -> Conversation:
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
    return await _chat(session, conversation)
