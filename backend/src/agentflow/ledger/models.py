"""Customer accounts and their transactions, per organization (tenant data, RLS)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from agentflow.db import Base, enum_check, str_enum


class TransactionKind(StrEnum):
    DEBIT = "debit"  # money out of the customer's wallet
    CREDIT = "credit"  # money in (incl. refunds)


class TransactionStatus(StrEnum):
    POSTED = "posted"
    REVERSED = "reversed"  # a refund credited it back; can't be refunded again


class Account(Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (UniqueConstraint("org_id", "customer_ref"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    customer_ref: Mapped[str] = mapped_column(String(100))  # the business's own customer id
    customer_name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Transaction(Base):
    __tablename__ = "ledger_transactions"
    __table_args__ = (
        enum_check("kind", TransactionKind),
        enum_check("status", TransactionStatus),
        UniqueConstraint("org_id", "reference"),
        Index("ix_ledger_transactions_account_id_occurred_at", "account_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ledger_accounts.id"))
    reference: Mapped[str] = mapped_column(String(50))  # e.g. TXN-88231
    kind: Mapped[TransactionKind] = mapped_column(str_enum(TransactionKind))
    # Money is Numeric (exact decimal), never float: 0.1 + 0.2 != 0.3 in binary floating point.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    merchant: Mapped[str] = mapped_column(String(200))
    status: Mapped[TransactionStatus] = mapped_column(
        str_enum(TransactionStatus), default=TransactionStatus.POSTED
    )
    # For a refund credit: the debit it reverses.
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ledger_transactions.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
