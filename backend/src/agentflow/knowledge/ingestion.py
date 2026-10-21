"""Ingestion: original file -> text -> chunks -> embeddings -> rows, for one document.

The expensive part (download, parse, embed) runs outside any database transaction; the
write is one short transaction that swaps the document's chunks all at once. Re-ingesting
(a retry, or a new embedding model) therefore never leaves a half-indexed document visible.
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.knowledge.chunking import ChunkDraft, chunk_text
from agentflow.knowledge.models import Chunk, Document, DocumentStatus
from agentflow.knowledge.models_runtime import Embedder
from agentflow.knowledge.parsing import extract_text
from agentflow.knowledge.screening import InjectionScreen, Verdict


@dataclass(frozen=True)
class PreparedChunks:
    drafts: list[ChunkDraft]
    vectors: list[list[float]]
    verdicts: list[Verdict]  # injection screening, one per chunk
    embedding_model: str

    @property
    def quarantined(self) -> int:
        return sum(v.flagged for v in self.verdicts)


async def prepare(
    data: bytes, content_type: str, embedder: Embedder, screen: InjectionScreen
) -> PreparedChunks:
    """Parse, chunk, embed and screen. Pure computation: no database, safe to retry."""
    text = await asyncio.to_thread(extract_text, data, content_type)
    drafts = chunk_text(text)
    if not drafts:
        return PreparedChunks([], [], [], embedder.model_name)
    vectors, verdicts = await asyncio.gather(
        embedder.embed_passages([d.embed_text for d in drafts]),
        screen.screen([d.embed_text for d in drafts]),
    )
    return PreparedChunks(drafts, vectors, verdicts, embedder.model_name)


async def replace_chunks(
    session: AsyncSession, document: Document, prepared: PreparedChunks
) -> None:
    """Swap the document's chunks and mark it ready, in the caller's transaction."""
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    session.add_all(
        Chunk(
            org_id=document.org_id,
            document_id=document.id,
            ordinal=draft.ordinal,
            section=draft.section,
            content=draft.content,
            embedding=vector,
            embedding_model=prepared.embedding_model,
            quarantined=verdict.flagged,
            quarantine_reason=verdict.reason,
        )
        for draft, vector, verdict in zip(
            prepared.drafts, prepared.vectors, prepared.verdicts, strict=True
        )
    )
    document.status = DocumentStatus.READY
    document.chunk_count = len(prepared.drafts)
    document.quarantined_chunks = prepared.quarantined
    document.error = None
    document.locked_until = None
