"""Embedding and reranking models behind small interfaces.

Retrieval code depends on `Embedder` / `Reranker`, never on fastembed, so tests use fakes
and production can switch to a hosted model (Bedrock, Cohere) without touching retrieval.

Both models run on the CPU (ONNX). Inference is CPU-bound, so calls run in a worker thread:
on the event loop they would stall every other request for the duration. Models load on
first use (seconds, plus a one-time download) so importing this module stays cheap.
"""

import asyncio
import threading
from collections.abc import Sequence
from typing import Protocol

from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder


class Embedder(Protocol):
    model_name: str

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    model_name: str

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """One relevance score per passage (higher = more relevant; scale is model-specific)."""
        ...


class FastEmbedEmbedder:
    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        self.model_name = model_name
        self._cache_dir = cache_dir
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()  # two first calls must not load the model twice

    def _get(self) -> TextEmbedding:
        with self._lock:
            if self._model is None:
                self._model = TextEmbedding(self.model_name, cache_dir=self._cache_dir)
            return self._model

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        def run() -> list[list[float]]:
            return [v.tolist() for v in self._get().passage_embed(list(texts), batch_size=64)]

        return await asyncio.to_thread(run)

    async def embed_query(self, text: str) -> list[float]:
        def run() -> list[float]:
            vector: list[float] = next(iter(self._get().query_embed([text]))).tolist()
            return vector

        return await asyncio.to_thread(run)


class FastEmbedReranker:
    """Cross-encoder: reads query and passage together, so it judges relevance far better
    than comparing two independent vectors, at the cost of running once per passage."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        self.model_name = model_name
        self._cache_dir = cache_dir
        self._model: TextCrossEncoder | None = None
        self._lock = threading.Lock()

    def _get(self) -> TextCrossEncoder:
        with self._lock:
            if self._model is None:
                self._model = TextCrossEncoder(self.model_name, cache_dir=self._cache_dir)
            return self._model

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []

        def run() -> list[float]:
            return [float(s) for s in self._get().rerank(query, list(passages))]

        return await asyncio.to_thread(run)
