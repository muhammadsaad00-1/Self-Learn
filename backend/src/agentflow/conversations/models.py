"""Customer chat: one conversation per customer, the messages in it (tenant data, RLS)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base, TimestampMixin, enum_check, str_enum


class ConversationMode(StrEnum):
    AI = "ai"  # each customer message runs the agents
    HUMAN = "human"  # a staff member took over: the agents stay quiet


class MessageRole(StrEnum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"  # the agents
    AGENT = "agent"  # a staff member


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("org_id", "customer_ref"),
        enum_check("mode", ConversationMode),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    customer_ref: Mapped[str] = mapped_column(String(100))
    mode: Mapped[ConversationMode] = mapped_column(
        str_enum(ConversationMode), default=ConversationMode.AI
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        enum_check("role", MessageRole),
        Index("ix_chat_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(str_enum(MessageRole))
    body: Mapped[str] = mapped_column(Text)
    # Structured extras the chat renders as cards: refund, transfer, case number, sources...
    card: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Customer message: the workflow it started. Assistant message: the workflow it reports.
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflows.id"), index=True)
    author_id: Mapped[uuid.UUID | None]  # staff member, for role=agent
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
