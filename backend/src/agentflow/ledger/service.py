"""Ledger operations used by the tools: look up a customer's transactions, find duplicate
debits, post a refund, and seed demo data. Money is Decimal everywhere."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.ledger.models import (
    Account,
    CardStatus,
    Transaction,
    TransactionKind,
    TransactionStatus,
    TransferStatus,
)
from agentflow.ledger.schemas import Evidence, TransactionOut

DUPLICATE_WINDOW = timedelta(minutes=10)


class AccountNotFoundError(Exception):
    pass


async def get_account(session: AsyncSession, org_id: uuid.UUID, customer_ref: str) -> Account:
    account = await session.scalar(
        select(Account).where(Account.org_id == org_id, Account.customer_ref == customer_ref)
    )
    if account is None:
        raise AccountNotFoundError
    return account


async def list_accounts(session: AsyncSession, org_id: uuid.UUID) -> list[Account]:
    return list(
        await session.scalars(
            select(Account).where(Account.org_id == org_id).order_by(Account.customer_ref)
        )
    )


async def recent_transactions(
    session: AsyncSession, account: Account, days: int = 30
) -> list[TransactionOut]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await session.scalars(
        select(Transaction)
        .where(
            Transaction.org_id == account.org_id,
            Transaction.account_id == account.id,
            Transaction.occurred_at >= since,
        )
        .order_by(Transaction.occurred_at.desc())
        .limit(100)
    )
    return [TransactionOut.model_validate(t) for t in rows]


def find_duplicates(
    transactions: list[TransactionOut],
) -> list[tuple[TransactionOut, TransactionOut]]:
    """(original, duplicate) pairs: posted debits with the same amount, currency and merchant
    within DUPLICATE_WINDOW of each other. Deterministic: this is evidence, not a guess."""
    debits = sorted(
        (t for t in transactions if t.kind == TransactionKind.DEBIT),
        key=lambda t: t.occurred_at,
    )
    pairs = []
    used: set[uuid.UUID] = set()
    for i, first in enumerate(debits):
        for later in debits[i + 1 :]:
            if later.occurred_at - first.occurred_at > DUPLICATE_WINDOW:
                break
            same = (first.amount, first.currency, first.merchant) == (
                later.amount,
                later.currency,
                later.merchant,
            )
            if same and first.id not in used and later.id not in used:
                pairs.append((first, later))
                used |= {first.id, later.id}
    return pairs


async def reversals_since(session: AsyncSession, account: Account, since: datetime) -> int:
    """Refund credits posted to this account since `since` (a policy input)."""
    count = await session.scalar(
        select(func.count())
        .select_from(Transaction)
        .where(
            Transaction.account_id == account.id,
            Transaction.kind == TransactionKind.CREDIT,
            Transaction.reverses_id.is_not(None),
            Transaction.occurred_at >= since,
        )
    )
    return count or 0


@dataclass(frozen=True)
class RefundPosted:
    original_reference: str
    refund_reference: str
    amount: Decimal
    currency: str


class RefundNotPossibleError(Exception):
    """The transaction can't be refunded (missing, not a debit, already reversed, ...)."""


async def post_refund(
    session: AsyncSession, account: Account, reference: str, amount: Decimal
) -> RefundPosted:
    """Credit `amount` back for debit `reference` and mark the debit reversed, in the
    caller's transaction. The row lock makes two concurrent refunds of one debit impossible:
    the second waits, then sees status 'reversed' and is refused."""
    debit = await session.scalar(
        select(Transaction)
        .where(
            Transaction.org_id == account.org_id,
            Transaction.account_id == account.id,
            Transaction.reference == reference,
        )
        .with_for_update()
    )
    if debit is None:
        raise RefundNotPossibleError(f"transaction {reference} not found on this account")
    if debit.kind != TransactionKind.DEBIT:
        raise RefundNotPossibleError(f"{reference} is not a debit")
    if debit.status == TransactionStatus.REVERSED:
        raise RefundNotPossibleError(f"{reference} was already refunded")
    if not Decimal("0") < amount <= debit.amount:
        raise RefundNotPossibleError(f"refund amount must be between 0 and {debit.amount}")
    debit.status = TransactionStatus.REVERSED
    refund = Transaction(
        org_id=account.org_id,
        account_id=account.id,
        reference=f"RFD-{uuid.uuid4().hex[:10].upper()}",
        kind=TransactionKind.CREDIT,
        amount=amount,
        currency=debit.currency,
        merchant=f"Refund of {reference}",
        reverses_id=debit.id,
        occurred_at=datetime.now(UTC),
    )
    session.add(refund)
    await session.flush()
    return RefundPosted(reference, refund.reference, amount, debit.currency)


async def freeze_card(session: AsyncSession, account: Account) -> bool:
    """Freeze the customer's card (idempotent). True if it was active until now."""
    locked = await session.scalar(select(Account).where(Account.id == account.id).with_for_update())
    assert locked is not None  # noqa: S101 (the caller loaded it in this transaction)
    was_active = locked.card_status == CardStatus.ACTIVE
    locked.card_status = CardStatus.FROZEN
    return was_active


@dataclass(frozen=True)
class Demo:
    """One demo transaction. Amounts starting with "+" are credits; `offset` is seconds
    after "3 hours ago". By default a payment the customer made themselves: from their
    registered phone, OTP-verified."""

    merchant: str
    amount: str
    offset: int
    transfer: TransferStatus | None = None
    channel: str = "card_online"
    device: str = "Registered phone"
    device_known: bool = True
    verified: bool = True


DAY = 86400
STRANGER = "Unrecognised device: Windows PC, IP in Lagos, Nigeria"

DEMO_CUSTOMERS: list[tuple[str, str, str, list[Demo]]] = [
    (
        "CUST-1001",
        "Ayesha Khan",
        "923001234501",
        [
            Demo("KE-Bills", "5000.00", 0, channel="app"),
            Demo("KE-Bills", "5000.00", 40, channel="app"),
            Demo("Daraz", "2350.00", 7200),
            Demo("Salary - Systems Ltd", "+120000.00", -5 * DAY, channel="ibft"),
        ],
    ),
    (
        "CUST-1002",
        "Bilal Ahmed",
        "923001234502",
        [
            Demo("Careem", "1200.00", 0),
            Demo("Careem", "1200.00", 25),
            Demo("Foodpanda", "850.00", 9000),
            Demo("Salary - Arbisoft", "+85000.00", -2 * DAY, channel="ibft"),
            Demo("RAAST from Ahmed Raza", "+4500.00", -DAY, TransferStatus.CREDITED, "raast"),
        ],
    ),
    (
        "CUST-1003",
        "Hina Raza",
        "923001234503",
        [
            # Genuine fraud: a new merchant, an unrecognised device abroad, no OTP, in a burst.
            Demo(
                "Unknown Merchant LLC",
                "45000.00",
                0,
                device=STRANGER,
                device_known=False,
                verified=False,
            ),
            Demo(
                "Unknown Merchant LLC",
                "40000.00",
                600,
                device=STRANGER,
                device_known=False,
                verified=False,
            ),
            Demo(
                "Imtiaz Supermarket", "3200.00", -2 * DAY, channel="pos", device="Card at the till"
            ),
            Demo("Salary - Jazz", "+95000.00", -3 * DAY, channel="ibft"),
        ],
    ),
    (
        "CUST-1004",
        "Usman Tariq",
        "923001234504",
        [
            Demo("IBFT to Ali Raza (HBL)", "5000.00", 0, TransferStatus.FAILED, "app"),
            # Reached the recipient: "my 3000 to Sara didn't arrive" is answered from records.
            Demo("RAAST to Sara Malik (Meezan)", "3000.00", -DAY, TransferStatus.CREDITED, "app"),
            Demo("Jazz top-up", "500.00", 3600, channel="app"),
            Demo("Microfinance loan disbursal", "+50000.00", -10 * DAY, channel="ibft"),
        ],
    ),
    (
        "CUST-1005",
        "Zainab Ali",
        "923001234505",
        [
            # Sent ~27 hours ago and still in transit: past the 24-hour window.
            Demo("IBFT to Kamran Shah (UBL)", "15000.00", -DAY, TransferStatus.PENDING, "app"),
        ],
    ),
    (
        "CUST-1006",
        "Faraz Hussain",
        "923001234506",
        [
            # Disputed as "never made", but everything says the customer made it: their own
            # phone, OTP-verified, at a merchant they buy from every month (first-party fraud).
            Demo("Daraz", "18500.00", -DAY + 3600),
            Demo("Daraz", "2100.00", -9 * DAY),
            Demo("Daraz", "3400.00", -16 * DAY),
            Demo("Daraz", "1850.00", -24 * DAY),
            Demo("Salary - Systems Ltd", "+110000.00", -6 * DAY, channel="ibft"),
        ],
    ),
]


async def seed_demo_data(
    session: AsyncSession, org_id: uuid.UUID, *, with_transactions: bool = True
) -> list[Account]:
    """Demo customers that exercise every policy path (idempotent: skips existing ones).

    CUST-1001  PKR 5,000 charged twice       -> refund above PKR 2,000: needs approval
    CUST-1002  PKR 1,200 charged twice       -> small refund: allowed automatically
    CUST-1003  unknown PKR 85,000 debits     -> genuine fraud: card frozen, a human investigates
    CUST-1004  PKR 5,000 transfer rejected   -> network-confirmed failure: reversed at once
    CUST-1005  PKR 15,000 transfer stuck 27h -> partner-bank inquiry ticket
    CUST-1006  "never made" PKR 18,500 Daraz -> looks customer-made: no freeze, reviewed
    Phones 923001234501..06 + usernames ayesha.khan, bilal.ahmed, hina.raza, usman.tariq,
    zainab.ali, faraz.hussain log in to the customer app (OTP); emails
    cust1001@example.com..cust1006@example.com write to the support inbox.
    """
    now = datetime.now(UTC)
    tag = uuid.uuid4().hex[:6].upper()
    accounts = []
    for ref, name, phone, txns in DEMO_CUSTOMERS:
        existing = await session.scalar(
            select(Account).where(Account.org_id == org_id, Account.customer_ref == ref)
        )
        if existing:
            accounts.append(existing)
            continue
        account = Account(
            org_id=org_id,
            customer_ref=ref,
            customer_name=name,
            phone=phone,
            username=name.lower().replace(" ", "."),  # "usman.tariq"
            email=f"{ref.lower().replace('-', '')}@example.com",
            currency="PKR",
        )
        session.add(account)
        await session.flush()
        for i, t in enumerate(txns if with_transactions else []):
            session.add(
                Transaction(
                    org_id=org_id,
                    account_id=account.id,
                    reference=f"TXN-{ref[-4:]}-{tag}-{i + 1}",
                    kind=TransactionKind.CREDIT if t.amount[0] == "+" else TransactionKind.DEBIT,
                    amount=Decimal(t.amount.lstrip("+")),
                    currency="PKR",
                    merchant=t.merchant,
                    transfer_status=t.transfer,
                    channel=t.channel,
                    device=t.device,
                    device_known=t.device_known,
                    verified=t.verified,
                    occurred_at=now - timedelta(hours=3) + timedelta(seconds=t.offset),
                )
            )
        accounts.append(account)
    await session.flush()
    return accounts


async def get_account_by_email(
    session: AsyncSession, org_id: uuid.UUID, email: str
) -> Account | None:
    return await session.scalar(
        select(Account).where(Account.org_id == org_id, Account.email == email.lower())
    )


async def gather_evidence(
    session: AsyncSession, org_id: uuid.UUID, customer_ref: str | None
) -> Evidence:
    """The research step's tool: the customer's recent ledger activity, duplicates found by
    code, and the reversal count the policy engine needs. Read-only."""
    if not customer_ref:
        return Evidence(
            customer_ref=None,
            customer_name=None,
            account_found=False,
            transactions=[],
            duplicate_pairs=[],
            reversals_last_24h=0,
        )
    try:
        account = await get_account(session, org_id, customer_ref)
    except AccountNotFoundError:
        return Evidence(
            customer_ref=customer_ref,
            customer_name=None,
            account_found=False,
            transactions=[],
            duplicate_pairs=[],
            reversals_last_24h=0,
        )
    transactions = await recent_transactions(session, account)
    return Evidence(
        customer_ref=customer_ref,
        customer_name=account.customer_name,
        account_found=True,
        card_status=account.card_status,
        transactions=transactions,
        duplicate_pairs=[(a.reference, b.reference) for a, b in find_duplicates(transactions)],
        reversals_last_24h=await reversals_since(
            session, account, datetime.now(UTC) - timedelta(days=1)
        ),
    )


async def get_account_by_phone(
    session: AsyncSession, org_id: uuid.UUID, phone: str
) -> Account | None:
    return await session.scalar(
        select(Account).where(Account.org_id == org_id, Account.phone == phone)
    )


async def upsert_customer(session: AsyncSession, org_id: uuid.UUID, data: dict[str, Any]) -> bool:
    """Insert, or update the customer with the same `customer_ref`. True if it was new.
    Fields absent from `data` are left as they are (an import can add phones later)."""
    account = await session.scalar(
        select(Account).where(
            Account.org_id == org_id, Account.customer_ref == data["customer_ref"]
        )
    )
    created = account is None
    if account is None:
        account = Account(
            org_id=org_id,
            customer_ref=data["customer_ref"],
            customer_name=data["customer_name"],
            currency=data.get("currency") or "PKR",
        )
        session.add(account)
    account.customer_name = data["customer_name"]
    for field in ("phone", "username", "email"):
        if data.get(field):
            setattr(account, field, data[field])
    await session.flush()
    return created


class UnknownCustomerError(Exception):
    pass


async def insert_transaction(
    session: AsyncSession, org_id: uuid.UUID, data: dict[str, Any]
) -> bool:
    """Record one transaction. False if its reference already exists (a re-imported
    statement must not double the money). Raises UnknownCustomerError."""
    account = await session.scalar(
        select(Account).where(
            Account.org_id == org_id, Account.customer_ref == data["customer_ref"]
        )
    )
    if account is None:
        raise UnknownCustomerError(data["customer_ref"])
    exists = await session.scalar(
        select(Transaction.id).where(
            Transaction.org_id == org_id, Transaction.reference == data["reference"]
        )
    )
    if exists:
        return False
    session.add(
        Transaction(
            org_id=org_id,
            account_id=account.id,
            reference=data["reference"],
            kind=TransactionKind(data["direction"]),
            amount=Decimal(data["amount"]),
            currency=data.get("currency") or account.currency,
            merchant=data["description"],
            transfer_status=TransferStatus(data["transfer_status"])
            if data.get("transfer_status")
            else None,
            channel=data.get("channel"),
            device=data.get("device"),
            device_known=data.get("device_known"),
            verified=data.get("verified"),
            occurred_at=data["occurred_at"],
        )
    )
    await session.flush()
    return True


async def existing_customer_refs(
    session: AsyncSession, org_id: uuid.UUID, refs: set[str]
) -> set[str]:
    rows = await session.scalars(
        select(Account.customer_ref).where(Account.org_id == org_id, Account.customer_ref.in_(refs))
    )
    return set(rows)
