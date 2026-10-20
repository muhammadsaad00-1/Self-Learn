"""The knowledge module's heavyweight collaborators, built once per process.

Bundled so the API and the worker build them the same way, and tests swap in fakes with one
object. Models load lazily (first search/ingestion), not at startup.
"""

from dataclasses import dataclass

from agentflow.config import Settings
from agentflow.knowledge.models_runtime import (
    Embedder,
    FastEmbedEmbedder,
    FastEmbedReranker,
    Reranker,
)
from agentflow.knowledge.retrieval import SearchParams
from agentflow.knowledge.screening import InjectionScreen, PromptGuardScreen
from agentflow.knowledge.storage import ObjectStore, S3ObjectStore


@dataclass(frozen=True)
class KnowledgeRuntime:
    embedder: Embedder
    reranker: Reranker
    store: ObjectStore
    screen: InjectionScreen
    params: SearchParams
    min_rerank_score: float


def search_params(settings: Settings) -> SearchParams:
    return SearchParams(
        candidates=settings.retrieval_candidates,
        rerank_candidates=settings.rerank_candidates,
        top_k=settings.retrieval_top_k,
        vector_weight=settings.rrf_vector_weight,
        keyword_weight=settings.rrf_keyword_weight,
    )


def build_runtime(settings: Settings) -> KnowledgeRuntime:
    return KnowledgeRuntime(
        embedder=FastEmbedEmbedder(settings.embedding_model, settings.model_cache_dir),
        reranker=FastEmbedReranker(settings.reranker_model, settings.model_cache_dir),
        store=S3ObjectStore(settings),
        screen=PromptGuardScreen(settings),
        params=search_params(settings),
        min_rerank_score=settings.min_rerank_score,
    )
