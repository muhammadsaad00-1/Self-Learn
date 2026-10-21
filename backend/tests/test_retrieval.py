"""Hybrid retrieval against real Postgres (pgvector + tsvector) as the app role.

Documents go through the real ingestion code (parse -> chunk -> embed -> replace_chunks)
with the fake lexical embedder, so ranks are deterministic.
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.db import set_tenant_context
from agentflow.identity.models import Organization
from agentflow.knowledge import retrieval
from agentflow.knowledge.ingestion import prepare, replace_chunks
from agentflow.knowledge.models import Chunk, Document, DocumentStatus
from agentflow.knowledge.retrieval import SearchParams, fuse
from agentflow.knowledge.screening import PatternScreen
from tests.fakes import FakeEmbedder, FakeReranker

POLICY = b"""\
# Customer policies
## Duplicate charges
If a payment is debited more than once, the duplicate debit is reversed within 3 business days.
## Fees
IBFT transfers to other banks are free for the first 5 per month, then PKR 15 each.
## Lost or stolen cards
Report a lost or stolen card in the app. The card is blocked within 1 hour.
"""

PARAMS = SearchParams(candidates=10, rerank_candidates=10, top_k=3)


async def ingest(
    session: AsyncSession,
    data: bytes = POLICY,
    status: DocumentStatus = DocumentStatus.READY,
    org_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Create an org (unless given) and one document, ingested through the real pipeline."""
    if org_id is None:
        org_id = uuid.uuid4()
        await set_tenant_context(session, org_id=org_id)
        session.add(Organization(id=org_id, name="EasyPay", slug=org_id.hex))
        await session.flush()
    document = Document(
        org_id=org_id,
        title="Customer policies",
        filename="policies.md",
        content_type="text/markdown",
        size_bytes=len(data),
        checksum=uuid.uuid4().hex,
        storage_key="k",
    )
    session.add(document)
    await session.flush()
    await replace_chunks(
        session, document, await prepare(data, "text/markdown", FakeEmbedder(), PatternScreen())
    )
    document.status = status
    await session.flush()
    return org_id


async def sections(session: AsyncSession, org_id: uuid.UUID, *queries: str) -> list[str | None]:
    hits = await retrieval.search(
        session,
        org_id,
        list(queries),
        embedder=FakeEmbedder(),
        reranker=FakeReranker(),
        params=PARAMS,
    )
    return [h.section for h in hits]


# --- Fusion (pure) -------------------------------------------------------------------------


def test_rrf_rewards_agreement_and_respects_weights() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    scores = fuse([([a, b], 1.0), ([b, c], 1.0)])
    assert max(scores, key=scores.__getitem__) == b  # found by both lists
    weighted = fuse([([a], 1.0), ([c], 0.5)])
    assert weighted[a] == pytest.approx(1 / 61)
    assert weighted[c] == pytest.approx(0.5 / 61)


# --- Search against Postgres ---------------------------------------------------------------


async def test_ingestion_stores_sections_and_vectors(app_session: AsyncSession) -> None:
    org_id = await ingest(app_session)
    rows = (
        await app_session.execute(
            select(Chunk.ordinal, Chunk.section, Chunk.embedding_model)
            .where(Chunk.org_id == org_id)
            .order_by(Chunk.ordinal)
        )
    ).all()
    assert [r.section for r in rows] == [
        "Customer policies > Duplicate charges",
        "Customer policies > Fees",
        "Customer policies > Lost or stolen cards",
    ]
    assert {r.embedding_model for r in rows} == {"fake-embedder"}


async def test_finds_the_relevant_section_first(app_session: AsyncSession) -> None:
    org_id = await ingest(app_session)
    assert (await sections(app_session, org_id, "what does an IBFT transfer cost"))[0] == (
        "Customer policies > Fees"
    )
    assert (await sections(app_session, org_id, "my card was stolen"))[0] == (
        "Customer policies > Lost or stolen cards"
    )


async def test_hits_carry_every_stage_rank(app_session: AsyncSession) -> None:
    org_id = await ingest(app_session)
    hits = await retrieval.search(
        app_session,
        org_id,
        ["IBFT"],
        embedder=FakeEmbedder(),
        reranker=FakeReranker(),
        params=PARAMS,
    )
    top = hits[0]
    assert top.section == "Customer policies > Fees"
    assert (top.keyword_rank, top.vector_rank) == (1, 1)
    assert top.fused_score > 0
    assert top.rerank_score == 1.0  # one query word ("ibft") found


async def test_multiple_queries_are_fused(app_session: AsyncSession) -> None:
    org_id = await ingest(app_session)
    found = await sections(app_session, org_id, "duplicate debit reversed", "card blocked")
    assert set(found[:2]) == {
        "Customer policies > Duplicate charges",
        "Customer policies > Lost or stolen cards",
    }


async def test_rerank_keeps_top_k(app_session: AsyncSession) -> None:
    org_id = await ingest(app_session)
    assert len(await sections(app_session, org_id, "card fee transfer debit")) == PARAMS.top_k


async def test_documents_not_ready_are_not_searchable(app_session: AsyncSession) -> None:
    org_id = await ingest(app_session, status=DocumentStatus.PROCESSING)
    assert await sections(app_session, org_id, "IBFT fee") == []


async def test_other_tenants_documents_are_never_returned(app_session: AsyncSession) -> None:
    secret = b"## Secret\nIBFT transfers for VIP clients are always free."
    other_org = await ingest(app_session, secret)
    my_org = await ingest(app_session)  # tenant context is now my org
    assert "Secret" not in str(await sections(app_session, my_org, "IBFT free VIP"))
    # Even asking for the other org's id returns nothing: RLS hides its rows.
    assert await sections(app_session, other_org, "IBFT free VIP") == []


async def test_reingest_replaces_chunks(app_session: AsyncSession) -> None:
    org_id = await ingest(app_session)
    document = await app_session.scalar(select(Document).where(Document.org_id == org_id))
    assert document is not None
    new = b"## Fees\nIBFT transfers cost PKR 10."
    await replace_chunks(
        app_session, document, await prepare(new, "text/plain", FakeEmbedder(), PatternScreen())
    )
    count = await app_session.scalar(select(func.count()).where(Chunk.document_id == document.id))
    assert (count, document.chunk_count) == (1, 1)


async def test_empty_query_list_returns_nothing(app_session: AsyncSession) -> None:
    org_id = await ingest(app_session)
    assert await sections(app_session, org_id) == []


async def test_stopword_only_query_does_not_break_keyword_search(
    app_session: AsyncSession,
) -> None:
    org_id = await ingest(app_session)
    assert await retrieval.keyword_ranked(app_session, org_id, "the and of", 5) == []
