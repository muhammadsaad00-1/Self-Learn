"""Hybrid retrieval: vector + keyword search, fused with weighted RRF, then reranked.

    query ──► vector search (pgvector HNSW, cosine)  ─┐
          └─► keyword search (tsvector, ts_rank_cd)  ─┴─► weighted RRF ─► cross-encoder ─► top k

Why each stage (numbers from the retrieval demo and `make eval-rag`):
- vector search finds paraphrases ("my plastic got stolen" -> "Lost or stolen cards");
- keyword search finds exact tokens embeddings blur ("IBFT", "TXN-88231");
- Reciprocal Rank Fusion combines the two lists using only ranks, so cosine scores and
  ts_rank values never need to be put on one scale. Keyword is weighted lower: on natural
  sentences it matches filler words ("transfer", "money");
- the cross-encoder reranker reads query and chunk together and fixes the final order.

Several queries (from the query planner) are fused the same way ("RAG-fusion"): a chunk
found by more queries and retrievers ranks higher.

Every query filters by org_id explicitly; row-level security is the second guard.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.knowledge.models import Chunk, Document, DocumentStatus
from agentflow.knowledge.models_runtime import Embedder, Reranker

RRF_K = 60  # standard constant: damps the gap between rank 1 and rank 2


@dataclass(frozen=True)
class SearchParams:
    candidates: int = 30  # per retriever per query
    rerank_candidates: int = 20  # fused chunks the cross-encoder scores
    top_k: int = 5
    vector_weight: float = 1.0
    keyword_weight: float = 0.6


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    section: str | None
    content: str
    vector_rank: int | None  # best rank over all queries, 1-based; None = not found
    keyword_rank: int | None
    fused_score: float
    rerank_score: float | None = None

    @property
    def text_for_rerank(self) -> str:
        return f"{self.section}\n{self.content}" if self.section else self.content


async def vector_ranked(
    session: AsyncSession, org_id: uuid.UUID, query_vector: Sequence[float], limit: int
) -> list[uuid.UUID]:
    """Chunk ids, nearest first.

    HNSW returns the nearest vectors *before* filters apply. For a small tenant in a big
    shared table, LIMIT 30 of all tenants' nearest chunks could leave only a few of ours.
    Iterative scans make pgvector keep walking the graph until enough rows pass the filter.
    'relaxed_order' trades exact ordering for speed, hence the outer ORDER BY.
    """
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    distance = Chunk.embedding.cosine_distance(list(query_vector))
    nearest = (
        select(Chunk.id, distance.label("distance"))
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.org_id == org_id,
            Document.status == DocumentStatus.READY,
            Chunk.quarantined.is_(False),  # flagged as prompt injection at ingestion
        )
        .order_by(distance)
        .limit(limit)
        .subquery()
    )
    rows = await session.scalars(select(nearest.c.id).order_by(nearest.c.distance))
    return list(rows)


async def keyword_ranked(
    session: AsyncSession, org_id: uuid.UUID, query_text: str, limit: int
) -> list[uuid.UUID]:
    """Chunk ids by full-text rank. plainto_tsquery ANDs every word, which matches almost
    nothing for a natural sentence; OR-ing the lexemes and letting ranking decide works."""
    rows = await session.scalars(
        text("""
            WITH q AS (
                SELECT replace(plainto_tsquery('english', :query)::text, '&', '|')::tsquery AS q
            )
            SELECT c.id FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.id = c.document_id, q
            WHERE c.org_id = :org_id AND d.status = 'ready' AND NOT c.quarantined
              AND c.search @@ q.q
            ORDER BY ts_rank_cd(c.search, q.q) DESC, c.id
            LIMIT :limit
        """),
        {"query": query_text, "org_id": org_id, "limit": limit},
    )
    return list(rows)


def fuse(ranked_lists: Sequence[tuple[Sequence[uuid.UUID], float]]) -> dict[uuid.UUID, float]:
    """Weighted Reciprocal Rank Fusion: score(chunk) = sum of weight / (RRF_K + rank)."""
    scores: dict[uuid.UUID, float] = {}
    for ids, weight in ranked_lists:
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (RRF_K + rank)
    return scores


async def _load(
    session: AsyncSession, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[Chunk, str]]:
    rows = await session.execute(
        select(Chunk, Document.title)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_(ids))
    )
    return {chunk.id: (chunk, title) for chunk, title in rows}


async def hybrid_candidates(
    session: AsyncSession,
    org_id: uuid.UUID,
    queries: Sequence[str],
    embedder: Embedder,
    params: SearchParams,
) -> list[RetrievedChunk]:
    """Fused candidates, best first (before reranking)."""
    lists: list[tuple[Sequence[uuid.UUID], float]] = []
    best_vector: dict[uuid.UUID, int] = {}
    best_keyword: dict[uuid.UUID, int] = {}
    for query in queries:
        vector_ids = await vector_ranked(
            session, org_id, await embedder.embed_query(query), params.candidates
        )
        keyword_ids = await keyword_ranked(session, org_id, query, params.candidates)
        lists += [(vector_ids, params.vector_weight), (keyword_ids, params.keyword_weight)]
        for ids, best in ((vector_ids, best_vector), (keyword_ids, best_keyword)):
            for rank, chunk_id in enumerate(ids, start=1):
                best[chunk_id] = min(best.get(chunk_id, rank), rank)

    scores = fuse(lists)
    ordered = sorted(scores, key=lambda cid: (-scores[cid], str(cid)))  # tie-break: stable
    loaded = await _load(session, ordered)
    return [
        RetrievedChunk(
            chunk_id=cid,
            document_id=loaded[cid][0].document_id,
            document_title=loaded[cid][1],
            section=loaded[cid][0].section,
            content=loaded[cid][0].content,
            vector_rank=best_vector.get(cid),
            keyword_rank=best_keyword.get(cid),
            fused_score=scores[cid],
        )
        for cid in ordered
        if cid in loaded
    ]


async def rerank(
    reranker: Reranker, query: str, candidates: Sequence[RetrievedChunk], top_k: int
) -> list[RetrievedChunk]:
    scores = await reranker.score(query, [c.text_for_rerank for c in candidates])
    scored = [replace(c, rerank_score=s) for c, s in zip(candidates, scores, strict=True)]
    return sorted(scored, key=lambda c: -(c.rerank_score or 0.0))[:top_k]


async def search(
    session: AsyncSession,
    org_id: uuid.UUID,
    queries: Sequence[str],
    *,
    embedder: Embedder,
    reranker: Reranker,
    params: SearchParams,
    rerank_query: str | None = None,
) -> list[RetrievedChunk]:
    """The full pipeline: top_k chunks, best first. `rerank_query` (default: the first
    query) is what the cross-encoder compares chunks against."""
    if not queries:
        return []
    candidates = await hybrid_candidates(session, org_id, queries, embedder, params)
    return await rerank(
        reranker, rerank_query or queries[0], candidates[: params.rerank_candidates], params.top_k
    )
