"""Shared background-job mechanics (workflows, document ingestion): claims and backoff.

Jobs live in Postgres rows with `attempts`, `available_at` and `locked_until`; a SECURITY
DEFINER function per job type claims the next due row with FOR UPDATE SKIP LOCKED (ADR 0010).
"""

import random
import uuid
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class Claim:
    """A job this worker now holds: which row, which tenant, which attempt."""

    job_id: uuid.UUID
    org_id: uuid.UUID
    attempt: int

    @property
    def workflow_id(self) -> uuid.UUID:  # readable alias at workflow call sites
        return self.job_id


def backoff(attempt: int, base_seconds: float = 5, cap_seconds: float = 300) -> timedelta:
    """Exponential backoff with jitter: ~5s, ~10s, ~20s ... capped at 5 minutes.

    The random factor ("jitter") spreads retries out, so many jobs that failed together
    (say, during a provider outage) don't all hit the provider again in the same second.
    """
    delay = min(cap_seconds, base_seconds * 2 ** (attempt - 1))
    return timedelta(seconds=delay * random.uniform(0.5, 1.0))  # noqa: S311 (not crypto)
