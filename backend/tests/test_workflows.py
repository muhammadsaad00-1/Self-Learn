"""Workflows end to end: the API (idempotency, tenancy) and the worker (claiming, retries,
dead-lettering, lost leases), against the real database with a fake LLM."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentflow.config import Settings
from agentflow.db import set_tenant_context
from agentflow.llm import LLMError
from agentflow.worker import process_next
from agentflow.workflow import service
from agentflow.workflow.graph import build_graph
from tests.fakes import FakeLLM, fake_runtime, query_plan, triage_result
from tests.helpers import Account, signup

Sessions = async_sessionmaker[AsyncSession]


def submit(
    client: TestClient, account: Account, message: str = "Charged twice", key: str | None = None
) -> Any:
    return client.post(
        "/v1/workflows",
        headers={**account.headers(), "Idempotency-Key": key or uuid.uuid4().hex},
        json={"message": message},
    )


async def run_once(sessions: Sessions, settings: Settings, llm: FakeLLM) -> bool:
    return await process_next(sessions, build_graph(llm), settings, fake_runtime())


def agents() -> FakeLLM:
    """Triage + query planner. With no documents uploaded, retrieval finds nothing and the
    workflow ends in an abstention without calling the answer agents."""
    return FakeLLM(triage_result(), query_plan("duplicate charge refund"))


# --- API ------------------------------------------------------------------------------


def test_create_requires_idempotency_key(client: TestClient) -> None:
    owner = signup(client)
    resp = client.post("/v1/workflows", headers=owner.headers(), json={"message": "hi"})
    assert resp.status_code == 422


def test_same_key_same_body_returns_the_original(client: TestClient) -> None:
    owner = signup(client)
    first = submit(client, owner, key="retry-key-0001")
    again = submit(client, owner, key="retry-key-0001")
    assert (first.status_code, again.status_code) == (202, 200)
    assert first.json()["id"] == again.json()["id"]
    assert len(client.get("/v1/workflows", headers=owner.headers()).json()) == 1


def test_same_key_different_body_is_rejected(client: TestClient) -> None:
    owner = signup(client)
    submit(client, owner, "Charged twice", key="retry-key-0002")
    assert submit(client, owner, "Card blocked", key="retry-key-0002").status_code == 409


def test_workflows_are_tenant_scoped(client: TestClient) -> None:
    alice, bob = signup(client), signup(client)
    wf_id = submit(client, alice).json()["id"]
    assert client.get(f"/v1/workflows/{wf_id}", headers=bob.headers()).status_code == 404
    assert client.get("/v1/workflows", headers=bob.headers()).json() == []
    # Same key in another org is a different workflow, not a conflict.
    assert submit(client, bob, "Card blocked", key="shared-key-0003").status_code == 202
    assert submit(client, alice, "Charged twice", key="shared-key-0003").status_code == 202


# --- Worker ---------------------------------------------------------------------------


async def test_worker_triages_and_records_steps(
    client: TestClient, settings: Settings, sessions: Sessions
) -> None:
    owner = signup(client)
    wf_id = submit(client, owner, "I was charged twice!").json()["id"]

    assert await run_once(sessions, settings, agents()) is True
    assert await run_once(sessions, settings, agents()) is False  # queue empty

    wf = client.get(f"/v1/workflows/{wf_id}", headers=owner.headers()).json()
    assert wf["status"] == "completed"
    assert wf["result"]["triage"]["category"] == "duplicate_charge"
    assert [(s["node"], s["status"]) for s in wf["steps"]] == [
        ("intake", "completed"),
        ("triage", "completed"),
        ("plan_queries", "completed"),
        ("retrieve", "completed"),
        ("abstain", "completed"),
    ]
    assert wf["steps"][1]["model"] == "fake-model"
    assert wf["steps"][1]["input_tokens"] == 100
    actions = [
        e["action"]
        for e in client.get("/v1/audit/events", headers=owner.headers()).json()["events"]
    ]
    assert actions[:2] == ["workflow.completed", "workflow.created"]
    summary = client.get("/v1/workflows", headers=owner.headers()).json()[0]
    assert (summary["category"], summary["urgency"]) == ("duplicate_charge", "normal")


async def test_failure_retries_with_backoff_then_dead_letters(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    exec_sql: Callable[[str, dict[str, object]], Awaitable[None]],
) -> None:
    owner = signup(client)
    wf_id = submit(client, owner).json()["id"]
    broken = FakeLLM(LLMError("all models down"))

    for attempt in (1, 2, 3):
        assert await run_once(sessions, settings, broken) is True
        wf = client.get(f"/v1/workflows/{wf_id}", headers=owner.headers()).json()
        if attempt < 3:
            assert wf["status"] == "queued"  # waiting for its backoff delay
            assert await run_once(sessions, settings, broken) is False  # not due yet
            await exec_sql("UPDATE workflows SET available_at = now() WHERE id = :i", {"i": wf_id})

    assert wf["status"] == "failed"
    assert wf["attempts"] == 3
    assert "all models down" in wf["error"]
    assert [s["status"] for s in wf["steps"]] == ["completed", "failed"] * 3  # intake, attempt


async def test_crashed_worker_lease_is_reclaimed(
    client: TestClient,
    settings: Settings,
    sessions: Sessions,
    exec_sql: Callable[[str, dict[str, object]], Awaitable[None]],
) -> None:
    owner = signup(client)
    wf_id = submit(client, owner).json()["id"]
    async with sessions() as s, s.begin():  # a worker claims it ... and dies
        claim = await service.claim_next(s, max_attempts=3, lease_seconds=60)
    assert claim is not None
    assert claim.attempt == 1
    assert await run_once(sessions, settings, agents()) is False  # lease valid

    await exec_sql(
        "UPDATE workflows SET locked_until = now() - interval '1 second' WHERE id = :i",
        {"i": wf_id},
    )
    assert await run_once(sessions, settings, agents()) is True
    wf = client.get(f"/v1/workflows/{wf_id}", headers=owner.headers()).json()
    assert (wf["status"], wf["attempts"]) == ("completed", 2)


async def test_late_result_from_a_lost_lease_is_discarded(
    client: TestClient, settings: Settings, sessions: Sessions
) -> None:
    """Worker A's lease expired and worker B re-claimed; A finishing late must not win."""
    owner = signup(client)
    submit(client, owner)
    async with sessions() as s, s.begin():
        stale = await service.claim_next(s, max_attempts=3, lease_seconds=0)
    async with sessions() as s, s.begin():
        fresh = await service.claim_next(s, max_attempts=3, lease_seconds=60)
    assert stale is not None
    assert fresh is not None
    assert fresh.attempt == 2

    async with sessions() as s, s.begin():
        await set_tenant_context(s, org_id=stale.org_id)
        assert await service.lock_if_still_ours(s, stale) is None
        assert await service.lock_if_still_ours(s, fresh) is not None


async def test_concurrent_workers_claim_different_workflows(
    client: TestClient, sessions: Sessions
) -> None:
    owner = signup(client)
    submit(client, owner, "first")
    submit(client, owner, "second")
    async with sessions() as a, a.begin(), sessions() as b, b.begin():
        # Both transactions are open at once: SKIP LOCKED makes B pass over A's row.
        claim_a = await service.claim_next(a, max_attempts=3, lease_seconds=60)
        claim_b = await service.claim_next(b, max_attempts=3, lease_seconds=60)
        assert claim_a is not None
        assert claim_b is not None
        assert claim_a.workflow_id != claim_b.workflow_id
