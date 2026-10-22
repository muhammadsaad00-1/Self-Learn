"""The agent workflows as LangGraph graphs (spec 03 §4).

A graph is a set of nodes (steps) connected by edges (what runs next), sharing one state
dict. Each node receives the state and returns only the keys it changes; LangGraph merges
them. Conditional edges choose the next node from the state.

Customer-request workflow, in two graphs (a human approval can take hours; no worker waits):

  ANALYSIS
    START -> intake -> triage -> plan_queries -> retrieve --(weak evidence)--> abstain --+
                                                    |                                    |
                                                    v                                    |
                                                  answer -> verify --(supported)---------+
                                                    ^          |                         |
                                                    +--(once)--+                         v
                                    research (ledger) -> resolve -> policy_gate -> END
  EXECUTION (immediately if allowed, else after a human decided the approval)
    START -> execute (re-authorized, exactly once) -> raise_ticket (if a human must own it)
          -> communicate -> END

    agents (LLM):        triage, plan_queries, answer, verify, resolve, communicate
    deterministic code:  intake, retrieve, abstain, citation checks, research, proposal
                         validation, policy_gate, execute

The knowledge-only graph (POST /v1/knowledge/ask) is the same chain starting at plan_queries.

Nodes stay free of database and HTTP code: retrieval arrives through the run config
(`configurable.search`), and the caller persists each node's output, so graphs are
unit-tested with a fake LLM and a fake search.
"""

import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agentflow.agents import communication, query_planner, resolution
from agentflow.agents import knowledge as knowledge_agents
from agentflow.agents import triage as triage_agent
from agentflow.agents.resolution import ActionType, ProposedAction
from agentflow.knowledge.retrieval import RetrievedChunk
from agentflow.knowledge.schemas import Citation, KnowledgeAnswer, Source
from agentflow.ledger.schemas import Evidence
from agentflow.llm import LLMResult, StructuredLLM
from agentflow.policy import engine as policy
from agentflow.policy import risk as risk_analysis
from agentflow.tools.refund import ExecutionResult
from agentflow.tools.tickets import TicketRef

MAX_MESSAGE_CHARS = 8000
MAX_ANSWER_ATTEMPTS = 2  # first answer + one retry with verifier feedback
EXCERPT_CHARS = 240

# (queries, rerank_query) -> best chunks. Injected per run: the worker and the API each bind
# it to their own tenant-scoped database session.
SearchFn = Callable[[Sequence[str], str], Awaitable[list[RetrievedChunk]]]


class WorkflowState(TypedDict, total=False):
    message: str  # what the agents read: the request (+ conversation context), normalized
    customer_message: str  # what the customer actually wrote (for people: tickets, UI)
    triage: dict[str, Any]  # TriageResult as JSON
    queries: list[str]  # from the query planner
    sources: list[dict[str, Any]]  # Source[] as JSON
    draft: dict[str, Any]  # DraftAnswer as JSON
    feedback: str | None  # verifier's objections, for the retry
    answer_attempts: int
    knowledge: dict[str, Any]  # KnowledgeAnswer as JSON: the final RAG outcome
    customer_ref: str | None  # the business's id for the customer, if the channel knows it
    evidence: dict[str, Any]  # ledger Evidence as JSON (research)
    proposal: dict[str, Any]  # validated ProposedAction (resolve)
    decision: dict[str, Any]  # PolicyDecision (policy_gate)
    risk: dict[str, Any]  # RiskAssessment (policy_gate): criticality, by code
    approval: dict[str, Any] | None  # {"id", "status"} once a human was asked
    execution: dict[str, Any]  # ExecutionResult (execute)
    ticket: dict[str, Any] | None  # TicketRef, when a human must own the case (raise_ticket)
    reply: str  # drafted customer reply (communicate)
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


def build_knowledge_nodes(
    graph: StateGraph[WorkflowState], llm: StructuredLLM, done: str = END
) -> None:
    """Add plan_queries -> retrieve -> answer -> verify (+ abstain) to `graph`; every
    outcome continues to `done`."""

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

    def route_after_verify(state: WorkflowState) -> str:
        return done if "knowledge" in state else "answer"

    graph.add_node("plan_queries", plan_queries)
    graph.add_node("retrieve", retrieve)
    graph.add_node("abstain", abstain)
    graph.add_node("answer", answer)
    graph.add_node("verify", verify)
    graph.add_edge("plan_queries", "retrieve")
    graph.add_conditional_edges("retrieve", route_after_retrieve, ["answer", "abstain"])
    graph.add_edge("abstain", done)
    graph.add_edge("answer", "verify")
    graph.add_conditional_edges("verify", route_after_verify, ["answer", done])


# Categories with an executable tool today, and those that only get a reply.
ACTIONABLE = {"duplicate_charge", "failed_payment", "refund_request", "unauthorized_transaction"}
# Reply-only categories. "other" (thanks, greetings, small talk) is answered, not escalated:
# anything serious hidden in it is caught by the criticality analysis, which opens a ticket.
INFORMATIONAL = {"general_inquiry", "other"}

ResearchFn = Callable[[str | None], Awaitable[Evidence]]
ExecuteFn = Callable[["WorkflowState"], Awaitable[ExecutionResult]]
OpenTicketFn = Callable[["WorkflowState", list[str]], Awaitable[TicketRef]]


def build_graph(llm: StructuredLLM) -> CompiledStateGraph[WorkflowState]:
    """ANALYSIS: intake -> triage -> knowledge agents -> research -> resolve -> policy_gate."""

    async def triage(state: WorkflowState) -> WorkflowState:
        result = await triage_agent.triage(llm, state["message"])
        return {"triage": result.value.model_dump(mode="json"), "node_meta": _meta(result)}

    async def research(state: WorkflowState, config: RunnableConfig) -> WorkflowState:
        """Ledger lookup through the read-only tool; duplicates are found by code."""
        lookup: ResearchFn = _configurable(config)["research"]
        start = time.perf_counter()
        evidence = await lookup(state.get("customer_ref"))
        return {
            "evidence": evidence.model_dump(mode="json"),
            "node_meta": {"latency_ms": round((time.perf_counter() - start) * 1000)},
        }

    async def resolve(state: WorkflowState) -> WorkflowState:
        evidence = Evidence.model_validate(state["evidence"])
        triage_result = state.get("triage", {})
        category = triage_result.get("category")
        meta: dict[str, Any] = {}
        answered = (state.get("knowledge") or {}).get("status") == "answered"
        if category not in ACTIONABLE and answered:
            # The company's documents answered it (a question, or a topic with no tool such
            # as "what documents do I need?"): no ledger reasoning, no escalation. Saves a
            # model call on the most common path; critical risk still opens a ticket.
            proposal = ProposedAction(
                action=ActionType.NO_ACTION,
                transaction_reference=None,
                amount=None,
                rationale="Answered from the company's documents",
            )
        elif evidence.account_found and (category in ACTIONABLE or category in INFORMATIONAL):
            # Account questions the documents can't answer ("did my salary arrive?") are
            # answered from the customer's ledger by the resolution agent.
            result = await resolution.propose(
                llm, state["message"], triage_result, state.get("knowledge"), evidence
            )
            proposal = resolution.validate(result.value, evidence, category)
            meta = _meta(result)
        elif category in INFORMATIONAL:
            proposal = ProposedAction(
                action=ActionType.NO_ACTION,
                transaction_reference=None,
                amount=None,
                rationale="Information request: answered from the policy documents",
            )
        else:
            why = (
                "the customer's account could not be identified"
                if category in ACTIONABLE
                else f"no automated action exists for '{category}' requests"
            )
            proposal = ProposedAction(
                action=ActionType.ESCALATE_TO_HUMAN,
                transaction_reference=None,
                amount=None,
                rationale=f"Escalated: {why}",
            )
        return {"proposal": proposal.model_dump(mode="json"), "node_meta": meta}

    async def policy_gate(state: WorkflowState) -> WorkflowState:
        triage_result = state.get("triage", {})
        proposal = ProposedAction.model_validate(state["proposal"])
        evidence = Evidence.model_validate(state["evidence"])
        claimed = (triage_result.get("details") or {}).get("amount")
        risk = risk_analysis.assess(
            message=state["message"],
            triage_urgency=triage_result.get("urgency"),
            category=triage_result.get("category"),
            amount_at_stake=max(
                (Decimal(str(a)) for a in (claimed, proposal.amount) if a is not None),
                default=None,
            ),
            recent_requests=evidence.recent_requests_72h,
        )
        decision = policy.evaluate(
            proposal,
            evidence=evidence,
            category=triage_result.get("category"),
            needs_human_review=bool(triage_result.get("needs_human_review")),
            knowledge_status=(state.get("knowledge") or {}).get("status"),
        )
        return {
            "decision": decision.model_dump(mode="json"),
            "risk": risk.model_dump(mode="json"),
            "node_meta": {},
        }

    graph = StateGraph(WorkflowState)
    graph.add_node("intake", intake)
    graph.add_node("triage", triage)
    graph.add_node("research", research)
    graph.add_node("resolve", resolve)
    graph.add_node("policy_gate", policy_gate)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "triage")
    graph.add_edge("triage", "plan_queries")
    build_knowledge_nodes(graph, llm, done="research")
    graph.add_edge("research", "resolve")
    graph.add_edge("resolve", "policy_gate")
    graph.add_edge("policy_gate", END)
    return graph.compile()


def build_execution_graph(llm: StructuredLLM) -> CompiledStateGraph[WorkflowState]:
    """EXECUTION: execute (re-authorized, exactly once) -> raise_ticket -> communicate."""

    async def execute(state: WorkflowState, config: RunnableConfig) -> WorkflowState:
        run: ExecuteFn = _configurable(config)["execute"]
        start = time.perf_counter()
        result = await run(state)
        return {
            "execution": result.model_dump(mode="json"),
            "node_meta": {"latency_ms": round((time.perf_counter() - start) * 1000)},
        }

    async def raise_ticket(state: WorkflowState, config: RunnableConfig) -> WorkflowState:
        """Code decides when a human must own the case (policy.ticket_reasons)."""
        triage_result = state.get("triage") or {}
        execution = state.get("execution") or {}
        decision = state.get("decision")
        reasons = policy.ticket_reasons(
            category=triage_result.get("category"),
            urgency=triage_result.get("urgency"),
            decision=policy.PolicyDecision.model_validate(decision) if decision else None,
            approval_status=(state.get("approval") or {}).get("status"),
            execution_status=execution.get("status"),
            execution_detail=execution.get("detail"),
            risk_critical=bool((state.get("risk") or {}).get("critical")),
            risk_signals=[s["detail"] for s in (state.get("risk") or {}).get("signals", [])],
        )
        if not reasons:
            return {"ticket": None, "node_meta": {}}
        open_ticket: OpenTicketFn = _configurable(config)["open_ticket"]
        ticket = await open_ticket(state, reasons)
        return {"ticket": ticket.model_dump(mode="json"), "node_meta": {}}

    async def communicate(state: WorkflowState) -> WorkflowState:
        execution = state.get("execution") or {}
        outcome = {
            "case_number": (state.get("ticket") or {}).get("code"),
            "action": (state.get("proposal") or {}).get("action"),
            "execution": {
                k: execution.get(k)
                for k in ("status", "detail", "refund_reference", "amount", "currency")
            },
            "approval": (state.get("approval") or {}).get("status"),
            "decision": (state.get("decision") or {}).get("outcome"),
            # The transactions the resolution cited (verified by code): the reply may state
            # them as facts.
            "records": records_of(state),
        }
        knowledge = state.get("knowledge") or {}
        policy_text = knowledge.get("answer") if knowledge.get("status") == "answered" else None
        proposal = state.get("proposal") or {}
        if proposal.get("action") == ActionType.ANSWER_FROM_RECORDS:
            # The records are the answer. Policy text here invites promises nobody executed
            # ("we'll reverse the duplicate" when the records show one charge).
            outcome["finding"] = proposal.get("rationale")
            policy_text = None
        result = await communication.draft_reply(llm, state["message"], outcome, policy_text)
        reply = result.value.message[: communication.MAX_REPLY_CHARS]
        return {"reply": reply, "node_meta": _meta(result)}

    graph = StateGraph(WorkflowState)
    graph.add_node("execute", execute)
    graph.add_node("raise_ticket", raise_ticket)
    graph.add_node("communicate", communicate)
    graph.add_edge(START, "execute")
    graph.add_edge("execute", "raise_ticket")
    graph.add_edge("raise_ticket", "communicate")
    graph.add_edge("communicate", END)
    return graph.compile()


RECORD_FIELDS = ("reference", "kind", "amount", "currency", "merchant", "status")


def records_of(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The ledger rows an answer_from_records proposal cites, as plain facts."""
    refs = set((state.get("proposal") or {}).get("record_references") or [])
    return [
        {k: t.get(k) for k in (*RECORD_FIELDS, "transfer_status", "occurred_at")}
        for t in (state.get("evidence") or {}).get("transactions") or []
        if t["reference"] in refs
    ]


@dataclass(frozen=True)
class WorkflowGraphs:
    analysis: CompiledStateGraph[WorkflowState]
    execution: CompiledStateGraph[WorkflowState]


def build_workflow_graphs(llm: StructuredLLM) -> WorkflowGraphs:
    return WorkflowGraphs(build_graph(llm), build_execution_graph(llm))


def build_knowledge_graph(llm: StructuredLLM) -> CompiledStateGraph[WorkflowState]:
    """Question answering only (no triage): plan_queries -> ... -> END."""
    graph = StateGraph(WorkflowState)
    build_knowledge_nodes(graph, llm)
    graph.add_edge(START, "plan_queries")
    return graph.compile()


# Hard stop for runaway graphs (spec 03 §12): LangGraph raises after this many node runs.
# Worst case today: intake, triage, plan, retrieve, (answer, verify) x 2, research, resolve,
# policy_gate = 11.
RECURSION_LIMIT = 15
