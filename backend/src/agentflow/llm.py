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
import openai
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from agentflow.config import Settings

log = logging.getLogger("agentflow.llm")


class TaskType(StrEnum):
    CLASSIFICATION = "classification"  # triage
    QUERY_PLANNING = "query_planning"  # rewrite a request into search queries
    GROUNDED_ANSWER = "grounded_answer"  # answer only from retrieved sources
    VERIFICATION = "verification"  # check an answer against its sources


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
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)
_RATE_LIMITED = (groq.RateLimitError, openai.RateLimitError)


class RoutedLLM:
    def __init__(
        self,
        routes: dict[TaskType, list[str]],
        make_model: StructuredModelFactory,
        *,
        cooldown_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._routes = routes
        self._make_model = make_model
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        # Rate-limited model -> when to try it again. Without this, every call during a
        # provider's quota outage first waits for that provider to say no again.
        self._cool_until: dict[str, float] = {}

    def _order(self, task: TaskType) -> list[str]:
        """The task's models, those cooling down after a rate limit moved to the end (still
        tried as a last resort: better slow than no answer)."""
        now = self._clock()
        models = self._routes[task]
        ready = [m for m in models if self._cool_until.get(m, 0) <= now]
        return ready + [m for m in models if m not in ready]

    async def generate[T: BaseModel](
        self, task: TaskType, schema: type[T], *, system: str, user: str
    ) -> LLMResult[T]:
        failures: list[str] = []
        for model_name in self._order(task):
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
                    if isinstance(e, _RATE_LIMITED):
                        self._cool_until[model_name] = self._clock() + self._cooldown_seconds
                    log.warning("llm.unavailable", extra={"model": model_name, "error": repr(e)})
                    break  # next model
                except (groq.BadRequestError, openai.BadRequestError) as e:  # invalid JSON
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
    """The production LLM. Model names are "provider:model"; no prefix means Groq.

    With a Cerebras key, the same gpt-oss models served by Cerebras join every route right
    after Groq's first choice: same model family, so answers behave the same, and a Groq
    quota limit (200k tokens/day on the free tier) no longer stops the agents.
    """
    if settings.groq_api_key is None:
        raise LLMError("GROQ_API_KEY is not set")
    groq_key, cerebras_key = settings.groq_api_key, settings.cerebras_api_key

    def make_model(name: str, schema: type[BaseModel]) -> _Invokable:
        provider, _, model = name.partition(":") if ":" in name else ("groq", "", name)
        # gpt-oss "thinks" before answering; our tasks need little of that, and thinking
        # tokens count against rate limits and latency.
        effort = "low" if "gpt-oss" in model else None
        chat: Any
        if provider == "cerebras":
            chat = ChatOpenAI(
                model=model,
                api_key=cerebras_key,
                base_url=settings.cerebras_base_url,
                temperature=0,
                reasoning_effort=effort,
                timeout=settings.llm_timeout_seconds,
                max_retries=1,
            )
        else:
            chat = ChatGroq(
                model_name=model,
                api_key=groq_key,
                temperature=0,  # same input, same answer: agent output should be repeatable
                reasoning_effort=effort,
                timeout=settings.llm_timeout_seconds,
                max_retries=1,  # the SDK retries a blip once; an outage moves to the next model
            )
        runnable: _Invokable = chat.with_structured_output(
            schema, method="json_schema", include_raw=True
        )
        return runnable

    def route(models: list[str]) -> list[str]:
        if cerebras_key is None:
            return models
        return [models[0], "cerebras:gpt-oss-120b", *models[1:]]

    fast, strong = route(settings.llm_fast_models), route(settings.llm_strong_models)
    routes = {
        TaskType.CLASSIFICATION: fast,
        TaskType.QUERY_PLANNING: fast,
        TaskType.GROUNDED_ANSWER: strong,
        TaskType.VERIFICATION: strong,
    }
    return RoutedLLM(routes, make_model, cooldown_seconds=settings.llm_cooldown_seconds)
