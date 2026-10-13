"""The agent workflow as a LangGraph graph (spec 03 §4).

A graph is a set of nodes (steps) connected by edges (what runs next), sharing one state
dict. Each node receives the state and returns only the keys it changes; LangGraph merges
them. Today: START -> intake -> triage -> END. Later phases add planning, research,
knowledge, resolution and the policy gate as further nodes and conditional edges.

Nodes stay free of database and HTTP code. The worker persists each node's output as a
workflow step, so the graph can be unit-tested with a fake LLM and no database.
"""

import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agentflow.agents import triage as triage_agent
from agentflow.llm import StructuredLLM

MAX_MESSAGE_CHARS = 8000


class WorkflowState(TypedDict, total=False):
    message: str  # the customer's request, normalized by intake
    triage: dict[str, Any]  # TriageResult as JSON
    # Model/tokens/latency of the node that just ran; the worker stores it on the step.
    node_meta: dict[str, Any]


async def intake(state: WorkflowState) -> WorkflowState:
    """Deterministic clean-up before any model sees the text."""
    message = re.sub(r"\s+", " ", state["message"]).strip()[:MAX_MESSAGE_CHARS]
    if not message:
        raise ValueError("empty message")
    return {"message": message, "node_meta": {}}


def build_graph(llm: StructuredLLM) -> CompiledStateGraph[WorkflowState]:
    """Compile the graph with its dependencies injected (a real or a fake LLM)."""

    async def triage(state: WorkflowState) -> WorkflowState:
        result = await triage_agent.triage(llm, state["message"])
        return {
            "triage": result.value.model_dump(mode="json"),
            "node_meta": {
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_ms": result.latency_ms,
            },
        }

    graph = StateGraph(WorkflowState)
    graph.add_node("intake", intake)
    graph.add_node("triage", triage)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "triage")
    graph.add_edge("triage", END)
    return graph.compile()


# Hard stop for runaway graphs (spec 03 §12): LangGraph raises after this many node runs.
RECURSION_LIMIT = 10
