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


async def test_rate_limited_model_cools_down_then_is_tried_again() -> None:
    """During a provider's quota outage, calls go straight to the backup instead of waiting
    for the exhausted provider to refuse again every time."""
    now = [0.0]
    primary = ScriptedModel(rate_limited(), ok("primary-back"))
    backup = ScriptedModel(ok("b1"), ok("b2"))
    llm = RoutedLLM(
        {TaskType.CLASSIFICATION: ["primary", "backup"]},
        lambda name, _schema: {"primary": primary, "backup": backup}[name],
        cooldown_seconds=60,
        clock=lambda: now[0],
    )
    assert (await generate(llm)).model == "backup"
    assert (await generate(llm)).model == "backup"  # primary skipped while cooling down
    assert len(primary.received) == 1
    now[0] = 61
    assert (await generate(llm)).value.label == "primary-back"


def test_cerebras_joins_the_routes_only_with_a_key() -> None:
    from pydantic import SecretStr

    from agentflow.config import Settings
    from agentflow.llm import build_llm

    without = build_llm(
        Settings(groq_api_key=SecretStr("g"), groq_api_keys=None, cerebras_api_key=None)
    )
    assert all(not m.startswith("cerebras:") for m in without._routes[TaskType.GROUNDED_ANSWER])
    with_key = build_llm(
        Settings(groq_api_key=SecretStr("g"), groq_api_keys=None, cerebras_api_key=SecretStr("c"))
    )
    strong = with_key._routes[TaskType.GROUNDED_ANSWER]
    assert strong[:2] == ["openai/gpt-oss-120b", "cerebras:gpt-oss-120b"]


async def test_all_rate_limited_is_a_provider_outage_not_a_bad_task() -> None:
    from agentflow.llm import ProviderUnavailableError

    with pytest.raises(ProviderUnavailableError):
        await generate(router(a=ScriptedModel(rate_limited()), b=ScriptedModel(rate_limited())))
    with pytest.raises(LLMError) as bad:
        await generate(router(a=ScriptedModel(invalid(), invalid())))
    assert not isinstance(bad.value, ProviderUnavailableError)


def test_extra_groq_keys_try_the_same_model_before_a_smaller_one() -> None:
    from pydantic import SecretStr

    from agentflow.config import Settings
    from agentflow.llm import build_llm

    llm = build_llm(
        Settings(
            groq_api_key=SecretStr("k1"), groq_api_keys=SecretStr("k2, k3"), cerebras_api_key=None
        )
    )
    assert llm._routes[TaskType.GROUNDED_ANSWER] == [
        "openai/gpt-oss-120b",
        "groq@2:openai/gpt-oss-120b",
        "groq@3:openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "groq@2:openai/gpt-oss-20b",
        "groq@3:openai/gpt-oss-20b",
    ]


def test_a_blank_key_means_no_provider() -> None:
    """Compose passes unset keys as "": that must not put a keyless provider on the routes."""
    from pydantic import SecretStr

    from agentflow.config import Settings
    from agentflow.llm import build_llm

    settings = Settings(groq_api_key=SecretStr("k1"), groq_api_keys="  ", cerebras_api_key="")
    assert (settings.groq_api_keys, settings.cerebras_api_key) == (None, None)
    assert not any("cerebras" in m for m in build_llm(settings)._routes[TaskType.CLASSIFICATION])


async def test_request_too_large_moves_to_the_next_model() -> None:
    request = httpx.Request("POST", "https://api.groq.com")
    too_large = groq.APIStatusError(
        "Request too large", response=httpx.Response(413, request=request), body=None
    )
    result = await generate(router(primary=ScriptedModel(too_large), backup=ScriptedModel(ok())))
    assert result.model == "backup"


async def test_an_answer_wrapped_in_the_schema_shape_is_unwrapped() -> None:
    echoed = AIMessage(content='{"description": "", "properties": {"label": "a"}, "required": []}')
    schema_only = AIMessage(content='{"properties": {"label": {"type": "string"}}}')
    reply = {"raw": echoed, "parsed": None, "parsing_error": ValueError("Failed to parse")}
    result = await generate(router(primary=ScriptedModel(reply)))
    assert (result.value.label, result.model) == ("a", "primary")

    # The schema itself echoed back (no answer inside) is still invalid: next model.
    bad = {"raw": schema_only, "parsed": None, "parsing_error": ValueError("Failed to parse")}
    result = await generate(router(primary=ScriptedModel(bad, bad), backup=ScriptedModel(ok())))
    assert result.model == "backup"
