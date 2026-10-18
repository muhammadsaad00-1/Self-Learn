"""Chunk storage in Postgres: vector similarity, keyword search, tenant isolation.

Uses hand-made vectors instead of a real model, so results are exact and the test is instant:
a vector pointing along axis 0 is "about topic 0", one along axis 1 is "about topic 1".
"""

import math
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.db import set_tenant_context
from agentflow.identity.models import Organization
from agentflow.knowledge.models import EMBEDDING_DIMENSIONS, Chunk, Document


def axis(*weights: float) -> list[float]:
    """A unit vector with the given leading components, zeros elsewhere."""
    v = [*weights, *[0.0] * (EMBEDDING_DIMENSIONS - len(weights))]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


async def new_org_with_document(
    session: AsyncSession,
    chunks: list[tuple[str, list[float]]],
    sections: list[str | None] | None = None,
) -> uuid.UUID:
    org_id = uuid.uuid4()
    await set_tenant_context(session, org_id=org_id)
    session.add(Organization(id=org_id, name="EasyPay", slug=org_id.hex))
    doc = Document(
        org_id=org_id,
        title="Refund policy",
        filename="refunds.md",
        content_type="text/markdown",
        size_bytes=100,
        checksum=uuid.uuid4().hex,
        storage_key=f"{org_id}/refunds.md",
    )
    session.add(doc)
    await session.flush()
    for i, (content, embedding) in enumerate(chunks):
        session.add(
            Chunk(
                org_id=org_id,
                document_id=doc.id,
                ordinal=i,
                section=sections[i] if sections else None,
                content=content,
                embedding=embedding,
                embedding_model="test",
            )
        )
    await session.flush()
    return org_id


async def test_nearest_chunks_by_cosine_distance(app_session: AsyncSession) -> None:
    await new_org_with_document(
        app_session,
        [
            ("about topic zero", axis(1, 0)),
            ("about topic one", axis(0, 1)),
            ("a bit of both", axis(1, 1)),
        ],
    )
    query = axis(1, 0.1)  # mostly topic zero
    # <=> is cosine *distance* (1 - similarity): smaller = closer, so ORDER BY ascending.
    distance = Chunk.embedding.cosine_distance(query)
    rows = (await app_session.execute(select(Chunk.content, distance).order_by(distance))).all()
    assert [r.content for r in rows] == ["about topic zero", "a bit of both", "about topic one"]
    assert rows[0][1] == pytest.approx(1 - 1 / math.sqrt(1.01), abs=1e-6)


async def test_keyword_search_matches_word_forms(app_session: AsyncSession) -> None:
    """The generated tsvector stems words: a search for "refunds" finds "refunded"."""
    await new_org_with_document(
        app_session,
        [
            ("Duplicate debits are refunded in full.", axis(1)),
            ("Card replacement costs PKR 500.", axis(0, 1)),
        ],
    )
    hits = await app_session.scalars(
        select(Chunk.content).where(
            Chunk.search.op("@@")(text("plainto_tsquery('english', 'refunds')"))
        )
    )
    assert hits.all() == ["Duplicate debits are refunded in full."]


async def test_heading_words_are_searchable_and_outrank_body_words(
    app_session: AsyncSession,
) -> None:
    """Regression for the retrieval demo: "charged twice" missed the chunk headed "Duplicate
    charges" because only the body was indexed, and its body never says "charge"."""
    await new_org_with_document(
        app_session,
        [
            ("If a payment is debited more than once, it is reversed.", axis(1)),
            ("Cash withdrawal charges at partner ATMs are PKR 25.", axis(0, 1)),
            ("Report a lost card in the app.", axis(0, 0, 1)),
        ],
        sections=["Duplicate charges", "Fees", "Lost cards"],
    )
    query = text("plainto_tsquery('english', 'charges')")
    rank = func.ts_rank(Chunk.search, query)
    hits = await app_session.execute(
        select(Chunk.section, rank).where(Chunk.search.op("@@")(query)).order_by(rank.desc())
    )
    # Both match "charges", but the heading match (weight A) ranks above the body match (B).
    assert [row.section for row in hits] == ["Duplicate charges", "Fees"]


async def test_chunks_of_another_org_are_invisible(app_session: AsyncSession) -> None:
    other = await new_org_with_document(app_session, [("other org's secret policy", axis(1))])
    await new_org_with_document(app_session, [("my policy", axis(1))])  # context now = my org
    assert (await app_session.scalars(select(Chunk.content))).all() == ["my policy"]
    with pytest.raises(DBAPIError, match="row-level security"):
        await app_session.execute(
            text(
                "INSERT INTO knowledge_chunks"
                " (org_id, document_id, ordinal, content, embedding, embedding_model)"
                " SELECT :o, document_id, 99, 'x', embedding, 'x' FROM knowledge_chunks LIMIT 1"
            ),
            {"o": other},
        )
