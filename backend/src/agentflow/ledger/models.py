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


class TransferStatus(StrEnum):
    """For transfers to another person (IBFT/RAAST): what the payment network reports."""

    CREDITED = "credited"  # the beneficiary bank credited the recipient
    PENDING = "pending"  # in transit: no answer from the beneficiary bank yet
    FAILED = "failed"  # the beneficiary bank rejected it: the money is owed back


class CardStatus(StrEnum):
    ACTIVE = "active"
    FROZEN = "frozen"  # blocked for new payments (reversible), e.g. on suspected fraud


class Account(Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint("org_id", "customer_ref"),
        UniqueConstraint("org_id", "phone"),
        UniqueConstraint("org_id", "email"),
        UniqueConstraint("org_id", "username"),
        enum_check("card_status", CardStatus),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    customer_ref: Mapped[str] = mapped_column(String(100))  # the business's own customer id
    customer_name: Mapped[str] = mapped_column(String(200))
    # Mobile number the customer logs in with (E.164 digits, e.g. 923001234501).
    phone: Mapped[str | None] = mapped_column(String(20))
    # Username the bank gave the customer (lowercase): with the phone, it identifies the
    # customer at login when they don't come through their bank's own link.
    username: Mapped[str | None] = mapped_column(String(64))
    # Email the customer writes from (the email channel matches senders to customers).
    email: Mapped[str | None] = mapped_column(String(320))
    currency: Mapped[str] = mapped_column(String(3))
    card_status: Mapped[CardStatus] = mapped_column(
        str_enum(CardStatus), default=CardStatus.ACTIVE, server_default=CardStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Transaction(Base):
    __tablename__ = "ledger_transactions"
    __table_args__ = (
        enum_check("kind", TransactionKind),
        enum_check("status", TransactionStatus),
        enum_check("transfer_status", TransferStatus),
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
    # How the payment was made, as card/payment systems record it: the evidence that settles
    # "I never made this payment" (dispute intelligence, policy/dispute.py).
    channel: Mapped[str | None] = mapped_column(String(32))  # card_online, pos, atm, app, ...
    device: Mapped[str | None] = mapped_column(String(200))  # "Registered phone (Samsung A54)"
    device_known: Mapped[bool | None]  # the customer's registered device
    verified: Mapped[bool | None]  # passed OTP / 3-D Secure
    # Set for transfers only (None for card/merchant payments).
    transfer_status: Mapped[TransferStatus | None] = mapped_column(str_enum(TransferStatus))
    # For a refund credit: the debit it reverses.
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ledger_transactions.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
