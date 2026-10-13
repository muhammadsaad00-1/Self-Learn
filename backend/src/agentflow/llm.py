"""LLM access for agents: route a task to models, get back schema-validated output.

Agents ask for "a CLASSIFICATION answer shaped like TriageResult" and never name a provider
or model, so models can be swapped in configuration (ADR 0002). Per call this module:

1. tries the task's models in order, moving on when one is rate-limited, slow or down;
2. asks for structured output: the provider constrains the answer to our JSON schema, and
   Pydantic validates it again, so agents only ever receive typed objects;
3. if an answer is invalid anyway, tells the model what was wrong and lets it retry once;
4. records model, tokens and latency for every call (FR-016: cost and performance tracking).
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import groq
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel

from agentflow.config import Settings

log = logging.getLogger("agentflow.llm")


class TaskType(StrEnum):
    CLASSIFICATION = "classification"


class LLMError(Exception):
    """No model produced a valid answer."""


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    value: T
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class StructuredLLM(Protocol):
    """What agents depend on. Tests pass a fake that implements just this method."""

    async def generate[T: BaseModel](
        self, task: TaskType, schema: type[T], *, system: str, user: str
    ) -> LLMResult[T]: ...


class _Invokable(Protocol):
    """A LangChain runnable (or a test fake): `await ainvoke(messages)` -> raw/parsed dict."""

    async def ainvoke(self, input: Any, /) -> Any: ...


# (model name, output schema) -> a runnable returning {"raw", "parsed", "parsing_error"}
StructuredModelFactory = Callable[[str, type[BaseModel]], _Invokable]

# Errors that say "this provider/model can't answer right now": try the next model.
_TRANSIENT = (
    groq.RateLimitError,
    groq.APIConnectionError,
    groq.APITimeoutError,
    groq.InternalServerError,
)


class RoutedLLM:
    def __init__(
        self, routes: dict[TaskType, list[str]], make_model: StructuredModelFactory
    ) -> None:
        self._routes = routes
        self._make_model = make_model

    async def generate[T: BaseModel](
        self, task: TaskType, schema: type[T], *, system: str, user: str
    ) -> LLMResult[T]:
        failures: list[str] = []
        for model_name in self._routes[task]:
            model = self._make_model(model_name, schema)
            # User text goes in its own message, never into the system instructions, so a
            # customer writing "ignore your rules" can't rewrite them (prompt injection).
            messages: list[BaseMessage] = [SystemMessage(system), HumanMessage(user)]
            tokens_in = tokens_out = 0
            start = time.perf_counter()
            for attempt in (1, 2):  # first answer + one repair
                try:
                    out: dict[str, Any] = await model.ainvoke(messages)
                except _TRANSIENT as e:
                    failures.append(f"{model_name}: {type(e).__name__}")
                    log.warning("llm.unavailable", extra={"model": model_name, "error": repr(e)})
                    break  # next model
                except groq.BadRequestError as e:  # e.g. the provider rejected invalid JSON
                    out = {"raw": None, "parsed": None, "parsing_error": e}
                usage = getattr(out["raw"], "usage_metadata", None) or {}
                tokens_in += usage.get("input_tokens", 0)
                tokens_out += usage.get("output_tokens", 0)
                parsed = out["parsed"]
                log.info(
                    "llm.call",
                    extra={
                        "task": task,
                        "model": model_name,
                        "attempt": attempt,
                        "valid": parsed is not None,
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                    },
                )
                if isinstance(parsed, schema):
                    return LLMResult(
                        value=parsed,
                        model=model_name,
                        input_tokens=tokens_in,
                        output_tokens=tokens_out,
                        latency_ms=round((time.perf_counter() - start) * 1000),
                    )
                failures.append(f"{model_name}: invalid output ({out['parsing_error']})")
                messages = [
                    *messages,
                    HumanMessage(
                        "Your previous answer did not match the required JSON schema "
                        f"({str(out['parsing_error'])[:300]}). Answer again with valid JSON only."
                    ),
                ]
        raise LLMError("; ".join(failures) or f"no models configured for {task}")


def build_llm(settings: Settings) -> RoutedLLM:
    """The production LLM: Groq models, routes from settings."""
    if settings.groq_api_key is None:
        raise LLMError("GROQ_API_KEY is not set")
    api_key = settings.groq_api_key

    def make_model(name: str, schema: type[BaseModel]) -> _Invokable:
        chat = ChatGroq(
            model_name=name,
            api_key=api_key,
            temperature=0,  # same input, same answer: classification should be repeatable
            # gpt-oss "thinks" before answering; classification needs little of that, and
            # thinking tokens count against rate limits and latency.
            reasoning_effort="low" if name.startswith("openai/gpt-oss") else None,
            timeout=settings.llm_timeout_seconds,
            max_retries=1,  # the SDK retries a blip once; a real outage moves to the next model
        )
        runnable: _Invokable = chat.with_structured_output(
            schema, method="json_schema", include_raw=True
        )
        return runnable

    return RoutedLLM({TaskType.CLASSIFICATION: settings.llm_classification_models}, make_model)
