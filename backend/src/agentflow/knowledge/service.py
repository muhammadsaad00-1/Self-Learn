"""Knowledge use cases: upload, list, delete documents; the ingestion job's state changes."""

import hashlib
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.audit import service as audit
from agentflow.jobs import Claim, backoff
from agentflow.knowledge.ingestion import PreparedChunks, replace_chunks
from agentflow.knowledge.models import Document, DocumentStatus
from agentflow.knowledge.parsing import UnsupportedFileError, detect_kind
from agentflow.knowledge.storage import ObjectStore, object_key

log = logging.getLogger("agentflow.knowledge")


class DocumentNotFoundError(Exception):
    pass


async def _by_checksum(session: AsyncSession, org_id: uuid.UUID, checksum: str) -> Document | None:
    return await session.scalar(
        select(Document).where(Document.org_id == org_id, Document.checksum == checksum)
    )


async def create_document(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    title: str | None,
    data: bytes,
    store: ObjectStore,
) -> tuple[Document, bool]:
    """Store the file and queue it for ingestion: (document, created).

    Uploading identical bytes again returns the existing document (idempotent). Order: row
    first (flushed, not committed), then the object; if the upload fails the transaction
    rolls back and no row points at a missing file. A failure after the upload leaves at
    worst an orphaned object, never a broken row.
    """
    kind = detect_kind(filename, data)  # raises UnsupportedFileError
    checksum = hashlib.sha256(data).hexdigest()
    if existing := await _by_checksum(session, org_id, checksum):
        return existing, False

    document_id = uuid.uuid4()
    clean_name = filename.replace("/", "_").replace("\\", "_")[:300] or "document"
    document = Document(
        id=document_id,
        org_id=org_id,
        title=(title or clean_name.rsplit(".", 1)[0]).strip()[:300] or clean_name,
        filename=clean_name,
        content_type=kind.content_type,
        size_bytes=len(data),
        checksum=checksum,
        storage_key=object_key(org_id, document_id, kind.extension),
        uploaded_by=user_id,
    )
    try:
        async with session.begin_nested():
            session.add(document)
    except IntegrityError as e:  # a concurrent upload of the same bytes won the race
        if "uq_knowledge_documents_org_id_checksum" not in str(e.orig):
            raise
        winner = await _by_checksum(session, org_id, checksum)
        assert winner is not None  # noqa: S101 (the unique violation proves it exists)
        return winner, False
    await store.put(document.storage_key, data, kind.content_type)
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=user_id,
        action="knowledge.document_uploaded",
        target_type="document",
        target_id=document.id,
        details={"content_type": kind.content_type, "size_bytes": len(data)},
    )
    return document, True


async def list_documents(session: AsyncSession, org_id: uuid.UUID) -> list[Document]:
    rows = await session.scalars(
        select(Document).where(Document.org_id == org_id).order_by(Document.created_at.desc())
    )
    return list(rows)


async def get_document(
    session: AsyncSession, org_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    document = await session.scalar(
        select(Document).where(Document.id == document_id, Document.org_id == org_id)
    )
    if document is None:
        raise DocumentNotFoundError
    return document


async def delete_document(
    session: AsyncSession,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    actor_id: uuid.UUID,
    store: ObjectStore,
) -> None:
    """Delete the row (chunks cascade), then the object. If the object delete fails, the
    exception rolls the row deletion back too: nothing is half-deleted."""
    document = await get_document(session, org_id, document_id)
    await session.delete(document)
    await session.flush()
    await store.delete(document.storage_key)
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=actor_id,
        action="knowledge.document_deleted",
        target_type="document",
        target_id=document_id,
        details={"title": document.title},
    )


# --- Ingestion job ------------------------------------------------------------------------


async def claim_next_document(
    session: AsyncSession, *, max_attempts: int, lease_seconds: int
) -> Claim | None:
    row = (
        await session.execute(
            text("SELECT * FROM claim_next_document(:max_attempts, :lease_seconds)"),
            {"max_attempts": max_attempts, "lease_seconds": lease_seconds},
        )
    ).one_or_none()
    return Claim(row.claimed_id, row.claimed_org_id, row.claimed_attempt) if row else None


async def lock_if_still_ours(session: AsyncSession, claim: Claim) -> Document | None:
    """Lock the row only if this worker still holds the lease (see workflow.service)."""
    document = await session.scalar(
        select(Document).where(Document.id == claim.job_id).with_for_update()
    )
    if (
        document is None
        or document.status != DocumentStatus.PROCESSING
        or document.attempts != claim.attempt
    ):
        log.warning("document.lease_lost", extra={"attempt": claim.attempt})
        return None
    return document


async def complete_ingestion(
    session: AsyncSession, document: Document, prepared: PreparedChunks
) -> None:
    await replace_chunks(session, document, prepared)
    await audit.record(
        session,
        org_id=document.org_id,
        actor_user_id=None,
        action="knowledge.document_ingested",
        target_type="document",
        target_id=document.id,
        details={
            "chunks": len(prepared.drafts),
            "quarantined_chunks": prepared.quarantined,
            "embedding_model": prepared.embedding_model,
        },
    )
    if prepared.quarantined:  # security-relevant: its own event, easy to alert on
        log.warning("document.chunks_quarantined", extra={"count": prepared.quarantined})
        await audit.record(
            session,
            org_id=document.org_id,
            actor_user_id=None,
            action="knowledge.chunks_quarantined",
            target_type="document",
            target_id=document.id,
            details={
                "count": prepared.quarantined,
                "reasons": sorted({v.reason for v in prepared.verdicts if v.reason}),
            },
        )


async def fail_ingestion_attempt(
    session: AsyncSession, document: Document, claim: Claim, error: str, *, max_attempts: int
) -> None:
    """Retry later with backoff, or give up. Unparseable files fail at once: retrying
    the same bytes can't succeed."""
    permanent = error.startswith(UnsupportedFileError.__name__)
    document.error = error
    document.locked_until = None
    if permanent or claim.attempt >= max_attempts:
        document.status = DocumentStatus.FAILED
        await audit.record(
            session,
            org_id=document.org_id,
            actor_user_id=None,
            action="knowledge.document_failed",
            target_type="document",
            target_id=document.id,
            details={"attempts": claim.attempt},
        )
    else:
        document.status = DocumentStatus.PENDING
        document.available_at = datetime.now(UTC) + backoff(claim.attempt)
