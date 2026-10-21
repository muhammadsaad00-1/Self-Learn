"""Prompt-injection screening at ingestion: quarantine chunks that try to instruct an AI.

The RAG eval found the gap: asked about a poisoned "partner bulletin", the answer agent
relayed its injected claim ("all refunds are instant, no approval required"). Prompts that
say "ignore instructions in sources" reduce this but are not a security boundary. So chunks
are screened once, when they are ingested; flagged chunks are stored but quarantined:
retrieval never returns them, so no model ever reads them.

Two independent layers, flagged if either fires:
- deterministic patterns: free, instant, and still working if the model is unavailable;
- Llama Prompt Guard 2 (a small classifier hosted on Groq) returns an injection probability
  per text, catching paraphrases no regex anticipates.
"""

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from groq import AsyncGroq

from agentflow.config import Settings

log = logging.getLogger("agentflow.knowledge.screening")

_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|rules|policies)",
    r"disregard (all |any )?(the )?(previous|prior|above|your) (instructions|rules|policies)",
    r"\b(system|developer) (prompt|notice|message|override)\b",
    r"\byou are now\b",
    r"\b(message|note|instructions?) (to|for) (ai|llm|language model|assistant)s?\b",
    r"\bact as (an? )?(admin|administrator|system|developer)\b",
]
_INJECTION = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


@dataclass(frozen=True)
class Verdict:
    flagged: bool
    reason: str | None


class InjectionScreen(Protocol):
    async def screen(self, texts: Sequence[str]) -> list[Verdict]: ...


class PatternScreen:
    """Deterministic layer only (tests, or no classifier configured)."""

    async def screen(self, texts: Sequence[str]) -> list[Verdict]:
        return [
            Verdict(True, "pattern") if looks_like_injection(t) else Verdict(False, None)
            for t in texts
        ]


class PromptGuardScreen:
    """Patterns + Llama Prompt Guard 2. If the classifier is unavailable the pattern layer
    still applies and a warning is logged: ingestion keeps working during an outage."""

    def __init__(self, settings: Settings, threshold: float = 0.5, concurrency: int = 4) -> None:
        key = settings.groq_api_key
        self._client = AsyncGroq(api_key=key.get_secret_value()) if key else None
        self._model = settings.prompt_guard_model
        self._threshold = threshold
        self._limit = asyncio.Semaphore(concurrency)

    async def _probability(self, text: str) -> float | None:
        if self._client is None:
            return None
        async with self._limit:
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    # The classifier reads ~512 tokens; the start of a chunk is what matters.
                    messages=[{"role": "user", "content": text[:2000]}],
                )
                return float(response.choices[0].message.content or "0")
            except Exception as e:  # outage, rate limit, unexpected output
                log.warning("screening.classifier_unavailable", extra={"error": repr(e)})
                return None

    async def screen(self, texts: Sequence[str]) -> list[Verdict]:
        probabilities = await asyncio.gather(*(self._probability(t) for t in texts))
        verdicts = []
        for text, p in zip(texts, probabilities, strict=True):
            if looks_like_injection(text):
                verdicts.append(Verdict(True, "pattern"))
            elif p is not None and p >= self._threshold:
                verdicts.append(Verdict(True, f"prompt-guard {p:.2f}"))
            else:
                verdicts.append(Verdict(False, None))
        return verdicts
