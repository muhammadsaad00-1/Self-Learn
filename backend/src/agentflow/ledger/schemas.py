import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from agentflow.ledger.models import TransactionKind, TransactionStatus


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_ref: str
    customer_name: str
    currency: str


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference: str
    kind: TransactionKind
    amount: Decimal
    currency: str
    merchant: str
    status: TransactionStatus
    occurred_at: datetime


class Evidence(BaseModel):
    """What the research step found in the ledger: the agents' factual basis."""

    customer_ref: str | None
    customer_name: str | None
    account_found: bool
    transactions: list[TransactionOut]
    duplicate_pairs: list[tuple[str, str]]  # (original reference, duplicate reference)
    reversals_last_24h: int
