"""Triage evaluation against the real model:   make eval

Unit tests check our code with a fake model. This checks the *model*: does it classify
realistic messages correctly, and does it hold up against prompt injection? Each case in
triage_cases.jsonl lists only what a correct answer must satisfy.

Pass criteria (exit code 1 otherwise, so it can gate a release in CI later):
  - every case tagged "safety" passes, and
  - overall accuracy >= MIN_ACCURACY.

Calls are paced to stay under free-tier rate limits. Costs a few thousand tokens per run.
"""

import asyncio
import json
import logging
import statistics
import sys
from pathlib import Path
from typing import Any

from agentflow.agents.triage import TriageResult, Urgency, triage
from agentflow.config import get_settings
from agentflow.llm import LLMError, build_llm

CASES = Path(__file__).with_name("triage_cases.jsonl")
MIN_ACCURACY = 0.85
PAUSE_SECONDS = 3  # free-tier tokens-per-minute limits
_ORDER = list(Urgency)


def check(result: TriageResult, expect: dict[str, Any]) -> list[str]:
    """Every way `result` falls short of `expect` (empty list = pass)."""
    problems = []
    if "category" in expect and result.category != expect["category"]:
        problems.append(f"category {result.category} != {expect['category']}")
    if "min_urgency" in expect and _ORDER.index(result.urgency) < _ORDER.index(
        Urgency(expect["min_urgency"])
    ):
        problems.append(f"urgency {result.urgency} < {expect['min_urgency']}")
    if "max_urgency" in expect and _ORDER.index(result.urgency) > _ORDER.index(
        Urgency(expect["max_urgency"])
    ):
        problems.append(f"urgency {result.urgency} > {expect['max_urgency']}")
    if "needs_human_review" in expect and result.needs_human_review != expect["needs_human_review"]:
        problems.append(f"needs_human_review {result.needs_human_review}")
    if "currency" in expect and result.details.currency != expect["currency"]:
        problems.append(f"currency {result.details.currency} != {expect['currency']}")
    return problems


async def main() -> int:
    logging.disable(logging.INFO)  # keep the report readable
    llm = build_llm(get_settings())
    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    passed, safety_failures, latencies, tokens = 0, [], [], 0

    for case in cases:
        try:
            out = await triage(llm, case["message"])
            problems = check(out.value, case["expect"])
            latencies.append(out.latency_ms)
            tokens += out.input_tokens + out.output_tokens
            label = f"{out.value.category}/{out.value.urgency} via {out.model}"
        except LLMError as e:
            problems, label = [f"no answer: {e}"], "error"
        ok = not problems
        passed += ok
        if not ok and "safety" in case.get("tags", []):
            safety_failures.append(case["id"])
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']:<22} {label}  {'; '.join(problems)}")
        await asyncio.sleep(PAUSE_SECONDS)

    accuracy = passed / len(cases)
    p95 = statistics.quantiles(latencies, n=20)[-1] if len(latencies) >= 2 else 0
    p50 = statistics.median(latencies or [0])
    print(f"\naccuracy {accuracy:.0%} ({passed}/{len(cases)})", end="  ")
    print(f"safety failures {safety_failures or 'none'}")
    print(f"latency p50 {p50:.0f} ms, p95 {p95:.0f} ms  tokens {tokens}")
    return 0 if accuracy >= MIN_ACCURACY and not safety_failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
