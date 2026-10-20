"""The agent workflows as LangGraph graphs (spec 03 §4).

A graph is a set of nodes (steps) connected by edges (what runs next), sharing one state
dict. Each node receives the state and returns only the keys it changes; LangGraph merges
them. Conditional edges choose the next node from the state.

Customer-request workflow (multi-agent RAG):

    START -> intake -> triage -> plan_queries -> retrieve --(weak evidence)--> abstain -> END
                                                    |
                                                    v
                                                  answer -> verify --(supported)--> END
                                                    ^          |
                                                    +--(once)--+  (unsupported: retry with
                                                                   the verifier's feedback,
                                                                   then "unverified" -> END)

    agents (LLM):        triage, plan_queries, answer, verify
    deterministic code:  intake, retrieve (hybrid search + rerank), abstain, citation checks

The knowledge-only graph (POST /v1/knowledge/ask) is the same chain starting at plan_queries.

Nodes stay free of database and HTTP code: retrieval arrives through the run config
(`configurable.search`), and the caller persists each node's output, so graphs are
unit-tested with a fake LLM and a fake search.
"""

import re
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agentflow.agents import knowledge as knowledge_agents
from agentflow.agents import query_planner
from agentflow.agents import triage as triage_agent
from agentflow.knowledge.retrieval import RetrievedChunk
from agentflow.knowledge.schemas import Citation, KnowledgeAnswer, Source
from agentflow.llm import LLMResult, StructuredLLM

MAX_MESSAGE_CHARS = 8000
MAX_ANSWER_ATTEMPTS = 2  # first answer + one retry with verifier feedback
EXCERPT_CHARS = 240

# (queries, rerank_query) -> best chunks. Injected per run: the worker and the API each bind
# it to their own tenant-scoped database session.
SearchFn = Callable[[Sequence[str], str], Awaitable[list[RetrievedChunk]]]


class WorkflowState(TypedDict, total=False):
    message: str  # the customer's request, normalized by intake
    triage: dict[str, Any]  # TriageResult as JSON
    queries: list[str]  # from the query planner
    sources: list[dict[str, Any]]  # Source[] as JSON
    draft: dict[str, Any]  # DraftAnswer as JSON
    feedback: str | None  # verifier's objections, for the retry
    answer_attempts: int
    knowledge: dict[str, Any]  # KnowledgeAnswer as JSON: the final RAG outcome
    # Model/tokens/latency of the node that just ran; the caller stores it on the step.
    node_meta: dict[str, Any]


def _meta(result: LLMResult[Any]) -> dict[str, Any]:
    return {
        "model": result.model,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "latency_ms": result.latency_ms,
    }


def _configurable(config: RunnableConfig) -> dict[str, Any]:
    values: dict[str, Any] = config.get("configurable", {})
    return values


def _sources(state: WorkflowState) -> list[Source]:
    return [Source.model_validate(s) for s in state.get("sources", [])]


def _final(state: WorkflowState, **fields: Any) -> WorkflowState:
    answer = KnowledgeAnswer(queries=state.get("queries", []), citations=[], **fields)
    return {"knowledge": answer.model_dump(mode="json"), "node_meta": {}}


async def intake(state: WorkflowState) -> WorkflowState:
    """Deterministic clean-up before any model sees the text."""
    message = re.sub(r"\s+", " ", state["message"]).strip()[:MAX_MESSAGE_CHARS]
    if not message:
        raise ValueError("empty message")
    return {"message": message, "node_meta": {}}


async def retrieve(state: WorkflowState, config: RunnableConfig) -> WorkflowState:
    """Hybrid search + rerank (no LLM). The planner's first query is what the cross-encoder
    compares against: for a Roman Urdu message it is the English rewrite."""
    search: SearchFn = _configurable(config)["search"]
    queries = state["queries"]
    start = time.perf_counter()
    chunks = await search(queries, queries[0])
    sources = [
        Source(
            number=i,
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            document_title=c.document_title,
            section=c.section,
            content=c.content,
            rerank_score=c.rerank_score,
        ).model_dump(mode="json")
        for i, c in enumerate(chunks, start=1)
    ]
    return {
        "sources": sources,
        "node_meta": {"latency_ms": round((time.perf_counter() - start) * 1000)},
    }


def route_after_retrieve(
    state: WorkflowState, config: RunnableConfig
) -> Literal["answer", "abstain"]:
    """Code, not the model, decides when the evidence is too weak to answer at all."""
    min_score: float = _configurable(config).get("min_rerank_score", float("-inf"))
    scores = [s.rerank_score for s in _sources(state) if s.rerank_score is not None]
    if not state.get("sources") or (scores and max(scores) < min_score):
        return "abstain"
    return "answer"


async def abstain(state: WorkflowState) -> WorkflowState:
    reason = (
        "No relevant policy documents found"
        if not state.get("sources")
        else "The closest policy passages are not relevant enough to answer"
    )
    return _final(state, status="abstained", answer=None, reason=reason)


def build_knowledge_nodes(graph: StateGraph[WorkflowState], llm: StructuredLLM) -> None:
    """Add plan_queries -> retrieve -> answer -> verify (+ abstain) to `graph`."""

    async def plan_queries(state: WorkflowState) -> WorkflowState:
        category = state.get("triage", {}).get("category")
        queries, result = await query_planner.plan_queries(llm, state["message"], category)
        return {"queries": queries, "answer_attempts": 0, "node_meta": _meta(result)}

    async def answer(state: WorkflowState) -> WorkflowState:
        sources = _sources(state)
        result = await knowledge_agents.draft_answer(
            llm, state["message"], sources, state.get("feedback")
        )
        return {
            "draft": result.value.model_dump(mode="json"),
            "answer_attempts": state.get("answer_attempts", 0) + 1,
            "node_meta": _meta(result),
        }

    async def verify(state: WorkflowState) -> WorkflowState:
        draft = knowledge_agents.DraftAnswer.model_validate(state["draft"])
        sources = _sources(state)
        if draft.abstain:
            return _final(state, status="abstained", answer=None, reason=draft.abstain_reason)

        meta: dict[str, Any] = {}
        problems = knowledge_agents.citation_problems(draft, len(sources))
        if not problems:  # only ask the verifier about answers that pass the code checks
            cited = [s for s in sources if s.number in set(draft.cited_sources)]
            result = await knowledge_agents.verify(llm, state["message"], draft.answer, cited)
            meta = _meta(result)
            if not result.value.supported:
                problems = [f"unsupported: {c}" for c in result.value.unsupported_claims] or [
                    "the verifier found unsupported claims"
                ]

        if not problems:
            citations = [
                Citation(
                    number=s.number,
                    chunk_id=s.chunk_id,
                    document_id=s.document_id,
                    document_title=s.document_title,
                    section=s.section,
                    excerpt=s.content[:EXCERPT_CHARS],
                )
                for s in sources
                if s.number in set(draft.cited_sources)
            ]
            answer = KnowledgeAnswer(
                status="answered",
                answer=draft.answer,
                citations=citations,
                queries=state.get("queries", []),
            )
            return {
                "knowledge": answer.model_dump(mode="json"),
                "feedback": None,
                "node_meta": meta,
            }

        feedback = "; ".join(problems)
        if state.get("answer_attempts", 0) < MAX_ANSWER_ATTEMPTS:
            return {"feedback": feedback, "node_meta": meta}  # route_after_verify -> answer
        unverified = _final(state, status="unverified", answer=draft.answer, reason=feedback)
        return {**unverified, "node_meta": meta}

    def route_after_verify(state: WorkflowState) -> Literal["answer", "__end__"]:
        return "__end__" if "knowledge" in state else "answer"

    graph.add_node("plan_queries", plan_queries)
    graph.add_node("retrieve", retrieve)
    graph.add_node("abstain", abstain)
    graph.add_node("answer", answer)
    graph.add_node("verify", verify)
    graph.add_edge("plan_queries", "retrieve")
    graph.add_conditional_edges("retrieve", route_after_retrieve, ["answer", "abstain"])
    graph.add_edge("abstain", END)
    graph.add_edge("answer", "verify")
    graph.add_conditional_edges("verify", route_after_verify, ["answer", END])


def build_graph(llm: StructuredLLM) -> CompiledStateGraph[WorkflowState]:
    """Customer-request workflow: intake -> triage -> knowledge agents."""

    async def triage(state: WorkflowState) -> WorkflowState:
        result = await triage_agent.triage(llm, state["message"])
        return {"triage": result.value.model_dump(mode="json"), "node_meta": _meta(result)}

    graph = StateGraph(WorkflowState)
    graph.add_node("intake", intake)
    graph.add_node("triage", triage)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "triage")
    graph.add_edge("triage", "plan_queries")
    build_knowledge_nodes(graph, llm)
    return graph.compile()


def build_knowledge_graph(llm: StructuredLLM) -> CompiledStateGraph[WorkflowState]:
    """Question answering only (no triage): plan_queries -> ... -> END."""
    graph = StateGraph(WorkflowState)
    build_knowledge_nodes(graph, llm)
    graph.add_edge(START, "plan_queries")
    return graph.compile()


# Hard stop for runaway graphs (spec 03 §12): LangGraph raises after this many node runs.
# Worst case today: intake, triage, plan, retrieve, (answer, verify) x 2 = 8.
RECURSION_LIMIT = 12
