"""HTTP endpoints: manage knowledge documents, search them, ask the knowledge agents."""

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status

from agentflow.db import SessionDep
from agentflow.identity.deps import AdminContextDep, OrgContextDep, SettingsDep
from agentflow.knowledge import retrieval, service
from agentflow.knowledge.parsing import UnsupportedFileError
from agentflow.knowledge.retrieval import RetrievedChunk
from agentflow.knowledge.runtime import KnowledgeRuntime, build_runtime
from agentflow.knowledge.schemas import (
    AskRequest,
    DocumentOut,
    KnowledgeAnswer,
    SearchHit,
    SearchRequest,
)
from agentflow.llm import StructuredLLM, build_llm
from agentflow.workflow.graph import RECURSION_LIMIT, build_knowledge_graph

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


def get_runtime(request: Request) -> KnowledgeRuntime:
    """Built on first use and kept for the process: loading models is slow."""
    if request.app.state.knowledge is None:
        request.app.state.knowledge = build_runtime(request.app.state.settings)
    runtime: KnowledgeRuntime = request.app.state.knowledge
    return runtime


def get_llm(request: Request) -> StructuredLLM:
    if request.app.state.llm is None:
        request.app.state.llm = build_llm(request.app.state.settings)
    llm: StructuredLLM = request.app.state.llm
    return llm


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    request: Request,
    ctx: AdminContextDep,
    session: SessionDep,
    settings: SettingsDep,
    response: Response,
    file: Annotated[UploadFile, File(description=".md, .txt or .pdf, up to 10 MB")],
    title: Annotated[str | None, Form(max_length=300)] = None,
) -> DocumentOut:
    """Upload a policy document (admins and owners). 202: stored and queued for ingestion;
    poll the list until it is `ready`. Uploading the same bytes again answers 200 with the
    existing document."""
    # Read one byte past the limit: enough to detect "too large" without buffering more.
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File is larger than 10 MB")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "File is empty")
    try:
        document, created = await service.create_document(
            session,
            org_id=ctx.org_id,
            user_id=ctx.user.id,
            filename=file.filename or "document",
            title=title,
            data=data,
            store=get_runtime(request).store,
        )
    except UnsupportedFileError as e:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(e)) from None
    if not created:
        response.status_code = status.HTTP_200_OK
    await session.flush()
    await session.refresh(document)
    return DocumentOut.model_validate(document)


@router.get("/documents")
async def list_documents(ctx: OrgContextDep, session: SessionDep) -> list[DocumentOut]:
    return [
        DocumentOut.model_validate(d) for d in await service.list_documents(session, ctx.org_id)
    ]


@router.get("/documents/{document_id}")
async def get_document(
    document_id: uuid.UUID, ctx: OrgContextDep, session: SessionDep
) -> DocumentOut:
    try:
        return DocumentOut.model_validate(
            await service.get_document(session, ctx.org_id, document_id)
        )
    except service.DocumentNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found") from None


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    request: Request, document_id: uuid.UUID, ctx: AdminContextDep, session: SessionDep
) -> Response:
    try:
        await service.delete_document(
            session, ctx.org_id, document_id, ctx.user.id, get_runtime(request).store
        )
    except service.DocumentNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search")
async def search(
    body: SearchRequest, request: Request, ctx: OrgContextDep, session: SessionDep
) -> list[SearchHit]:
    """Raw retrieval (no LLM): the reranked top chunks with every stage's rank and score.
    For debugging retrieval quality and for the dashboard's search box."""
    runtime = get_runtime(request)
    hits = await retrieval.search(
        session,
        ctx.org_id,
        [body.query],
        embedder=runtime.embedder,
        reranker=runtime.reranker,
        params=runtime.params,
    )
    return [SearchHit(**h.__dict__) for h in hits]


@router.post("/ask")
async def ask(
    body: AskRequest,
    request: Request,
    ctx: OrgContextDep,
    session: SessionDep,
    settings: SettingsDep,
) -> KnowledgeAnswer:
    """Ask the knowledge agents a question: query planner -> hybrid retrieval + rerank ->
    answer agent -> verifier. Answers only from this organization's documents, with
    citations; abstains when they don't cover the question."""
    # Each question costs 3-5 model calls: cap it per user (fixed window in Redis).
    redis = request.app.state.redis
    key = f"knowledge-ask:{ctx.user.id}"
    await redis.set(key, 0, ex=60, nx=True)
    if await redis.incr(key) > settings.knowledge_asks_per_minute:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many questions, try again in a minute",
            headers={"Retry-After": str(await redis.ttl(key))},
        )
    runtime = get_runtime(request)

    async def search_fn(queries: Sequence[str], rerank_query: str) -> list[RetrievedChunk]:
        return await retrieval.search(
            session,
            ctx.org_id,
            queries,
            embedder=runtime.embedder,
            reranker=runtime.reranker,
            params=runtime.params,
            rerank_query=rerank_query,
        )

    state = await build_knowledge_graph(get_llm(request)).ainvoke(
        {"message": " ".join(body.question.split())},
        config={
            "configurable": {"search": search_fn, "min_rerank_score": runtime.min_rerank_score},
            "recursion_limit": RECURSION_LIMIT,
        },
    )
    return KnowledgeAnswer.model_validate(state["knowledge"])
