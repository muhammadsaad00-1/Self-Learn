"""API and workflow-result shapes for the knowledge module."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict

from agentflow.knowledge.models import DocumentStatus
from agentflow.validation import non_blank


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    error: str | None
    chunk_count: int
    quarantined_chunks: int  # flagged as prompt injection, never retrieved
    created_at: datetime
    updated_at: datetime


class Source(BaseModel):
    """A retrieved chunk as shown to the answer agent, numbered for citation."""

    number: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    section: str | None
    content: str
    rerank_score: float | None


class Citation(BaseModel):
    number: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    section: str | None
    excerpt: str  # start of the cited chunk, so a human can check it quickly


class KnowledgeAnswer(BaseModel):
    """Outcome of the knowledge agents for one question.

    answered:   the answer passed citation checks and the verifier.
    abstained:  no good-enough sources (decided by code), or the answer agent declined.
    unverified: an answer exists but failed verification twice; a human must check it.
    """

    status: Literal["answered", "abstained", "unverified"]
    answer: str | None
    citations: list[Citation]
    queries: list[str]  # what was actually searched, for transparency and debugging
    reason: str | None = None


class SearchRequest(BaseModel):
    query: Annotated[str, non_blank(500)]


class SearchHit(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    section: str | None
    content: str
    vector_rank: int | None
    keyword_rank: int | None
    fused_score: float
    rerank_score: float | None


class AskRequest(BaseModel):
    question: Annotated[str, non_blank(2000)]
