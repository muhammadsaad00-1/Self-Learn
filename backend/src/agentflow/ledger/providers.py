"""Write-side adapter boundary between governed tools and a money system.

The current adapter is the local PostgreSQL demo ledger. A live bank/payment integration
must send ``idempotency_key`` to the provider and expose lookup/reconciliation for ambiguous
timeouts before it can replace this adapter.
"""

import uuid
from decimal import Decimal
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.ledger import service
from agentflow.ledger.models import Account


class MoneyActions(Protocol):
    async def refund(
        self,
        session: AsyncSession,
        *,
        org_id: uuid.UUID,
        customer_ref: str,
        transaction_reference: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> service.RefundPosted: ...

    async def freeze_card(
        self,
        session: AsyncSession,
        *,
        org_id: uuid.UUID,
        customer_ref: str,
        idempotency_key: str,
    ) -> bool: ...


class DatabaseLedgerAdapter:
    """Demo implementation: changes only AgentFlow's tenant-scoped sample ledger."""

    async def refund(
        self,
        session: AsyncSession,
        *,
        org_id: uuid.UUID,
        customer_ref: str,
        transaction_reference: str,
        amount: Decimal,
        idempotency_key: str,
    ) -> service.RefundPosted:
        # The application action ledger supplies deduplication in this simulator. A provider
        # adapter must forward this same stable key as the provider's idempotency key.
        del idempotency_key
        account = await service.get_account(session, org_id, customer_ref)
        return await service.post_refund(session, account, transaction_reference, amount)

    async def freeze_card(
        self,
        session: AsyncSession,
        *,
        org_id: uuid.UUID,
        customer_ref: str,
        idempotency_key: str,
    ) -> bool:
        del idempotency_key
        account: Account = await service.get_account(session, org_id, customer_ref)
        return await service.freeze_card(session, account)


money_actions: MoneyActions = DatabaseLedgerAdapter()
