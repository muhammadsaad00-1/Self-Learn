import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from agentflow.ledger.models import CardStatus, TransactionKind, TransactionStatus, TransferStatus


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_ref: str
    customer_name: str
    currency: str
    card_status: CardStatus


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference: str
    kind: TransactionKind
    amount: Decimal
    currency: str
    merchant: str
    status: TransactionStatus
    transfer_status: TransferStatus | None = None
    occurred_at: datetime


class Evidence(BaseModel):
    """What the research step found in the ledger: the agents' factual basis."""

    customer_ref: str | None
    customer_name: str | None
    account_found: bool
    card_status: CardStatus | None = None
    transactions: list[TransactionOut]
    duplicate_pairs: list[tuple[str, str]]  # (original reference, duplicate reference)
    reversals_last_24h: int
    # Earlier requests from this customer in 72h (set by the worker from the workflow
    # module): repeat contact is a criticality signal.
    recent_requests_72h: int = 0
