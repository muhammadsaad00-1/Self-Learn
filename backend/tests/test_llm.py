"""RoutedLLM: fallback between models, one repair attempt, usage accounting."""

from typing import Any

import groq
import httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel

from agentflow.llm import LLMError, RoutedLLM, TaskType


class Answer(BaseModel):
    label: str


def raw(input_tokens: int = 10, output_tokens: int = 5) -> AIMessage:
    return AIMessage(
        content="{}",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )


def rate_limited() -> groq.RateLimitError:
    request = httpx.Request("POST", "https://api.groq.com")
    return groq.RateLimitError(
        "rate limited", response=httpx.Response(429, request=request), body=None
    )


class ScriptedModel:
    """Plays back one scripted reply per call: a dict like LangChain returns, or an exception."""

    def __init__(self, *replies: dict[str, Any] | Exception) -> None:
        self.replies = list(replies)
        self.received: list[list[BaseMessage]] = []

    async def ainvoke(self, messages: list[BaseMessage], /) -> dict[str, Any]:
        self.received.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def router(**models: ScriptedModel) -> RoutedLLM:
    return RoutedLLM({TaskType.CLASSIFICATION: list(models)}, lambda name, _schema: models[name])


def ok(label: str = "x") -> dict[str, Any]:
    return {"raw": raw(), "parsed": Answer(label=label), "parsing_error": None}


def invalid() -> dict[str, Any]:
    return {"raw": raw(), "parsed": None, "parsing_error": ValueError("missing field 'label'")}


async def generate(llm: RoutedLLM) -> Any:
    return await llm.generate(TaskType.CLASSIFICATION, Answer, system="sys", user="hello")


async def test_first_model_answers() -> None:
    result = await generate(router(primary=ScriptedModel(ok("a")), backup=ScriptedModel()))
    assert (result.value.label, result.model, result.input_tokens) == ("a", "primary", 10)


async def test_falls_back_when_rate_limited() -> None:
    result = await generate(
        router(primary=ScriptedModel(rate_limited()), backup=ScriptedModel(ok("b")))
    )
    assert (result.value.label, result.model) == ("b", "backup")


async def test_repairs_invalid_output_once_and_counts_both_calls() -> None:
    primary = ScriptedModel(invalid(), ok("fixed"))
    result = await generate(router(primary=primary))
    assert result.value.label == "fixed"
    assert result.input_tokens == 20  # tokens of both calls are paid for, so both count
    repair_prompt = primary.received[1][-1].content
    assert "did not match the required JSON schema" in str(repair_prompt)


async def test_user_text_never_enters_system_message() -> None:
    primary = ScriptedModel(ok())
    await generate(router(primary=primary))
    system, user = primary.received[0]
    assert (system.type, system.content) == ("system", "sys")
    assert (user.type, user.content) == ("human", "hello")


async def test_gives_up_when_every_model_fails() -> None:
    llm = router(primary=ScriptedModel(invalid(), invalid()), backup=ScriptedModel(rate_limited()))
    with pytest.raises(LLMError, match=r"primary: invalid output.*backup: RateLimitError"):
        await generate(llm)
