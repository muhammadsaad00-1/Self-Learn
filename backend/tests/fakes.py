"""Test doubles. FakeLLM stands in for the real model so tests are fast, free and repeatable."""

from typing import Any

from pydantic import BaseModel

from agentflow.agents.triage import Category, Details, TriageResult, Urgency
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


class FakeLLM:
    """Answers with the scripted results in order (the last one repeats). An Exception in the
    script is raised instead, to simulate a provider failure."""

    def __init__(self, *script: BaseModel | Exception) -> None:
        self.script = list(script)
        self.calls: list[dict[str, str]] = []

    async def generate[T: BaseModel](
        self, task: TaskType, schema: type[T], *, system: str, user: str
    ) -> LLMResult[T]:
        self.calls.append({"task": task, "system": system, "user": user})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, schema)
        return LLMResult(
            value=item, model="fake-model", input_tokens=100, output_tokens=20, latency_ms=5
        )
