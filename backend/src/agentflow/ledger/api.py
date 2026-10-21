"""Read access to the simulated ledger for the dashboard, plus demo data for sandboxes."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from agentflow.db import SessionDep
from agentflow.identity.deps import AdminContextDep, OrgContextDep
from agentflow.knowledge import service as knowledge
from agentflow.knowledge.api import get_runtime
from agentflow.ledger import service
from agentflow.ledger.schemas import AccountOut, TransactionOut

router = APIRouter(prefix="/v1/ledger", tags=["ledger"])

DEMO_DOCS = Path(__file__).parent / "demo_docs"


@router.get("/accounts")
async def list_accounts(ctx: OrgContextDep, session: SessionDep) -> list[AccountOut]:
    return [AccountOut.model_validate(a) for a in await service.list_accounts(session, ctx.org_id)]


@router.get("/accounts/{customer_ref}/transactions")
async def list_transactions(
    customer_ref: str, ctx: OrgContextDep, session: SessionDep
) -> list[TransactionOut]:
    try:
        account = await service.get_account(session, ctx.org_id, customer_ref)
    except service.AccountNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found") from None
    return await service.recent_transactions(session, account)


@router.post("/demo-data", status_code=status.HTTP_201_CREATED)
async def seed_demo_data(
    request: Request, ctx: AdminContextDep, session: SessionDep
) -> list[AccountOut]:
    """Demo customers covering every policy path, plus the customer help guide (cheques,
    transfers, fraud, ...) queued for ingestion (admins; never in production)."""
    if request.app.state.settings.env == "prod":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    accounts = await service.seed_demo_data(session, ctx.org_id)
    for path in sorted(DEMO_DOCS.glob("*.md")):
        await knowledge.create_document(  # idempotent by checksum
            session,
            org_id=ctx.org_id,
            user_id=ctx.user.id,
            filename=path.name,
            title=None,
            data=path.read_bytes(),
            store=get_runtime(request).store,
        )
    return [AccountOut.model_validate(a) for a in accounts]
