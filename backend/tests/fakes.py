"""Test doubles: fast, free and deterministic stand-ins for models and storage."""

import hashlib
import math
import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from agentflow.agents.communication import CustomerReply
from agentflow.agents.knowledge import DraftAnswer, Verification
from agentflow.agents.query_planner import QueryPlan
from agentflow.agents.resolution import ActionType, ProposedAction
from agentflow.agents.triage import Category, Details, TriageResult, Urgency
from agentflow.knowledge.models import EMBEDDING_DIMENSIONS
from agentflow.knowledge.retrieval import SearchParams
from agentflow.knowledge.runtime import KnowledgeRuntime
from agentflow.knowledge.screening import PatternScreen
from agentflow.llm import LLMResult, TaskType


def triage_result(**overrides: Any) -> TriageResult:
    values: dict[str, Any] = {
        "category": Category.DUPLICATE_CHARGE,
        "urgency": Urgency.NORMAL,
        "summary": "Customer was charged twice for one transfer.",
        "details": Details(
            amount=None, currency=None, transaction_reference=None, transaction_date=None
        ),
        "confidence": 0.9,
        "needs_human_review": False,
        "reasoning": "Two identical debits are described.",
    }
    return TriageResult(**(values | overrides))


def query_plan(*queries: str, language: str = "English") -> QueryPlan:
    return QueryPlan(language=language, queries=list(queries) or ["duplicate charge refund"])


def draft(
    answer: str = "Duplicates are reversed within 3 business days.", *cited: int
) -> DraftAnswer:
    return DraftAnswer(
        answer=answer, cited_sources=list(cited) or [1], abstain=False, abstain_reason=None
    )


def abstention(reason: str = "The sources don't cover this.") -> DraftAnswer:
    return DraftAnswer(answer="", cited_sources=[], abstain=True, abstain_reason=reason)


def verdict(supported: bool = True, *claims: str) -> Verification:
    return Verification(supported=supported, unsupported_claims=list(claims))


def reply(message: str = "We are looking into your request.") -> CustomerReply:
    return CustomerReply(message=message)


def proposal(
    reference: str | None = None, amount: str | None = None, action: str = "refund_transaction"
) -> ProposedAction:
    return ProposedAction(
        action=ActionType(action),
        transaction_reference=reference,
        amount=Decimal(amount) if amount else None,
        rationale="The second debit duplicates the first.",
    )


class FakeLLM:
    """Answers each call with the next scripted object of the requested schema; the last one
    of a schema repeats. An Exception in the script is raised for any schema instead, to
    simulate a provider failure. Every call is recorded for assertions."""

    def __init__(self, *script: BaseModel | Exception) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def calls_for(self, schema: type[BaseModel]) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["schema"] is schema]

    async def generate[T: BaseModel](
        self, task: TaskType, schema: type[T], *, system: str, user: str
    ) -> LLMResult[T]:
        self.calls.append({"task": task, "schema": schema, "system": system, "user": user})
        matching = [i for i, x in enumerate(self.script) if isinstance(x, schema | Exception)]
        if not matching:
            raise AssertionError(f"FakeLLM has no scripted {schema.__name__}")
        i = matching[0]
        item = self.script.pop(i) if len(matching) > 1 else self.script[i]
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, schema)
        return LLMResult(
            value=item, model="fake-model", input_tokens=100, output_tokens=20, latency_ms=5
        )


_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class FakeEmbedder:
    """Hashed bag of words: texts sharing words get similar unit vectors. Lexical, not
    semantic, but deterministic and exercises the real vector path in Postgres."""

    model_name = "fake-embedder"

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * EMBEDDING_DIMENSIONS
        for word in words(text):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBEDDING_DIMENSIONS] += 1.0  # noqa: S324
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v] if any(v) else [1.0, *[0.0] * (EMBEDDING_DIMENSIONS - 1)]

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FakeReranker:
    """Scores a passage by how many distinct query words it contains."""

    model_name = "fake-reranker"

    async def score(self, query: str, passages: Sequence[str]) -> list[float]:
        q = set(words(query))
        return [float(len(q & set(words(p)))) for p in passages]


class MemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_next_get: Exception | None = None

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        if self.fail_next_get is not None:
            error, self.fail_next_get = self.fail_next_get, None
            raise error
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def fake_runtime(min_rerank_score: float = 1.0, top_k: int = 5) -> KnowledgeRuntime:
    """With FakeReranker, a score of 1.0 means "shares at least one word with the query"."""
    return KnowledgeRuntime(
        embedder=FakeEmbedder(),
        reranker=FakeReranker(),
        store=MemoryObjectStore(),
        screen=PatternScreen(),
        params=SearchParams(candidates=10, rerank_candidates=10, top_k=top_k),
        min_rerank_score=min_rerank_score,
    )
