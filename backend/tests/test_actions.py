"""The full action path end to end (real database, fake models):

request -> triage -> knowledge -> research -> resolve -> policy -> [approval] -> refund -> reply
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentflow.config import Settings
from agentflow.db import set_tenant_context
from agentflow.knowledge.runtime import KnowledgeRuntime
from agentflow.tools.models import ActionExecution
from agentflow.tools.refund import authorize_and_execute
from agentflow.worker import process_next, process_next_document
from agentflow.workflow.graph import build_workflow_graphs
from tests.fakes import (
    FakeLLM,
    draft,
    proposal,
    query_plan,
    reply,
    triage_result,
    verdict,
)
from tests.helpers import Account, signup

Sessions = async_sessionmaker[AsyncSession]
ExecSql = Callable[[str, dict[str, object]], Awaitable[None]]

POLICY = b"""# Refund policy
## Duplicate charges
A duplicate debit is reversed in full within 3 business days of the customer reporting it.
"""


def agents(
    reference: str = "", amount: str = "", category: str = "duplicate_charge", **kw: Any
) -> FakeLLM:
    """Scripted agents for one workflow: triage, planner, answer + verifier, resolution, reply."""
    return FakeLLM(
        triage_result(category=category, **kw),
        query_plan("duplicate debit reversal"),
        draft("A duplicate debit is reversed in full within 3 business days.", 1),
        verdict(),
        proposal(reference or None, amount or None),
        reply("Your refund has been processed."),
    )


async def setup_org(
    client: TestClient, sessions: Sessions, settings: Settings, runtime: KnowledgeRuntime
) -> Account:
    """An org with the refund policy ingested and the demo ledger seeded."""
    owner = signup(client)
    client.post(
        "/v1/knowledge/documents",
        headers=owner.headers(),
        files={"file": ("refunds.md", POLICY, "text/markdown")},
    )
    while await process_next_document(sessions, settings, runtime):
        pass
    assert client.post("/v1/ledger/demo-data", headers=owner.headers()).status_code == 201
    return owner


def duplicate_of(client: TestClient, owner: Account, customer_ref: str) -> dict[str, Any]:
    """The later debit of the customer's duplicate pair (what a refund should target)."""
    txns = client.get(
        f"/v1/ledger/accounts/{customer_ref}/transactions", headers=owner.headers()
    ).json()
    debits = sorted((t for t in txns if t["kind"] == "debit"), key=lambda t: t["occurred_at"])
    first, second = debits[0], debits[1]
    assert (first["amount"], first["merchant"]) == (second["amount"], second["merchant"])
    result: dict[str, Any] = second
    return result


def submit(
    client: TestClient, account: Account, customer_ref: str, message: str = "I was charged twice!"
) -> str:
    resp = client.post(
        "/v1/workflows",
        headers={**account.headers(), "Idempotency-Key": uuid.uuid4().hex},
        json={"message": message, "customer_ref": customer_ref},
    )
    assert resp.status_code == 202, resp.text
    workflow_id: str = resp.json()["id"]
    return workflow_id


async def run(
    sessions: Sessions, settings: Settings, runtime: KnowledgeRuntime, llm: FakeLLM
) -> bool:
    return await process_next(sessions, build_workflow_graphs(llm), settings, runtime)


def workflow(client: TestClient, account: Account, wf_id: str) -> dict[str, Any]:
    result: dict[str, Any] = client.get(f"/v1/workflows/{wf_id}", headers=account.headers()).json()
    return result


def make_admin(client: TestClient, owner: Account) -> Account:
    checker = signup(client)
    client.post(
        "/v1/members", headers=owner.headers(), json={"email": checker.email, "role": "admin"}
    )
    return Account(checker.email, checker.token, checker.user_id, owner.org_id)


async def credits(sessions: Sessions, org_id: str) -> int:
    async with sessions() as s, s.begin():
        await set_tenant_context(s, org_id=uuid.UUID(org_id))
        return (
            await s.scalar(text("SELECT count(*) FROM ledger_transactions WHERE kind = 'credit'"))
            or 0
        )


# --- Allowed automatically ----------------------------------------------------------------


async def test_small_duplicate_is_refunded_automatically(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents()
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        dup = duplicate_of(client, owner, "CUST-1002")  # PKR 1,200
        llm.script[4] = proposal(dup["reference"], dup["amount"])
        wf_id = submit(client, owner, "CUST-1002")
        assert await run(sessions, settings, runtime, llm)

        wf = workflow(client, owner, wf_id)
        assert wf["status"] == "completed"
        r = wf["result"]
        assert r["decision"]["outcome"] == "allow"
        assert r["execution"]["status"] == "executed"
        assert r["execution"]["verified"] is True
        assert r["reply"] == "Your refund has been processed."
        assert [s["node"] for s in wf["steps"]][-6:] == [
            "research",
            "resolve",
            "policy_gate",
            "execute",
            "raise_ticket",
            "communicate",
        ]
        assert r["ticket"] is None  # resolved automatically: nobody needs to own it
        txns = client.get(
            "/v1/ledger/accounts/CUST-1002/transactions", headers=owner.headers()
        ).json()
        assert next(t for t in txns if t["reference"] == dup["reference"])["status"] == "reversed"
        actions = [
            e["action"]
            for e in client.get("/v1/audit/events", headers=owner.headers()).json()["events"]
        ]
        assert "tool.refund_executed" in actions


# --- Approval required: pause, maker-checker, resume ---------------------------------------


async def test_large_refund_waits_for_a_second_officer(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents()
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        dup = duplicate_of(client, owner, "CUST-1001")  # PKR 5,000
        llm.script[4] = proposal(dup["reference"], dup["amount"])
        wf_id = submit(client, owner, "CUST-1001")
        assert await run(sessions, settings, runtime, llm)

        wf = workflow(client, owner, wf_id)
        assert wf["status"] == "waiting_approval"
        assert wf["result"]["decision"]["rules"] == ["maker_checker"]
        assert await credits(sessions, owner.org_id) == 0  # nothing paid yet
        assert await run(sessions, settings, runtime, llm) is False  # not claimable while waiting

        [approval] = client.get("/v1/approvals?status=pending", headers=owner.headers()).json()
        assert (approval["workflow_id"], approval["customer_ref"]) == (wf_id, "CUST-1001")

        # Maker-checker: the officer who submitted the request can't approve it.
        resp = client.post(
            f"/v1/approvals/{approval['id']}/approve", headers=owner.headers(), json={}
        )
        assert resp.status_code == 403
        assert "Maker-checker" in resp.json()["detail"]

        checker = make_admin(client, owner)
        resp = client.post(
            f"/v1/approvals/{approval['id']}/approve",
            headers=checker.headers(),
            json={"note": "Verified"},
        )
        assert resp.status_code == 200
        # A decision is final.
        assert (
            client.post(
                f"/v1/approvals/{approval['id']}/reject", headers=checker.headers(), json={}
            ).status_code
            == 409
        )

        assert await run(sessions, settings, runtime, llm)  # resumes at the execution stage
        r = workflow(client, owner, wf_id)["result"]
        assert (r["approval"]["status"], r["execution"]["status"]) == ("approved", "executed")
        assert await credits(sessions, owner.org_id) == 1


async def test_rejected_refund_is_not_executed(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents()
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        dup = duplicate_of(client, owner, "CUST-1001")
        llm.script[4] = proposal(dup["reference"], dup["amount"])
        wf_id = submit(client, owner, "CUST-1001")
        await run(sessions, settings, runtime, llm)
        [approval] = client.get("/v1/approvals", headers=owner.headers()).json()
        checker = make_admin(client, owner)
        client.post(
            f"/v1/approvals/{approval['id']}/reject",
            headers=checker.headers(),
            json={"note": "Not a duplicate"},
        )
        await run(sessions, settings, runtime, llm)

        wf = workflow(client, owner, wf_id)
        assert wf["status"] == "completed"
        assert wf["result"]["execution"] == {
            **wf["result"]["execution"],
            "status": "not_executed",
            "detail": "Approval rejected",
        }
        assert await credits(sessions, owner.org_id) == 0


async def test_expired_approval_is_not_executed(
    client_for: Any,
    settings: Settings,
    sessions: Sessions,
    runtime: KnowledgeRuntime,
    exec_sql: ExecSql,
) -> None:
    llm = agents()
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        dup = duplicate_of(client, owner, "CUST-1001")
        llm.script[4] = proposal(dup["reference"], dup["amount"])
        wf_id = submit(client, owner, "CUST-1001")
        await run(sessions, settings, runtime, llm)
        await exec_sql("UPDATE approvals SET expires_at = now() - interval '1 minute'", {})
        async with sessions() as s, s.begin():  # the worker's sweep, as the app role
            assert await s.scalar(text("SELECT expire_approvals()")) == 1
        await run(sessions, settings, runtime, llm)

        r = workflow(client, owner, wf_id)["result"]
        assert (r["approval"]["status"], r["execution"]["detail"]) == (
            "expired",
            "Approval expired",
        )
        assert await credits(sessions, owner.org_id) == 0


async def test_limits_are_rechecked_at_execution_time(
    client_for: Any,
    settings: Settings,
    sessions: Sessions,
    runtime: KnowledgeRuntime,
    exec_sql: ExecSql,
) -> None:
    """An approval is permission to try: if the customer hit the daily reversal limit while
    the approval waited, the refund is still refused."""
    llm = agents()
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        dup = duplicate_of(client, owner, "CUST-1001")
        llm.script[4] = proposal(dup["reference"], dup["amount"])
        wf_id = submit(client, owner, "CUST-1001")
        await run(sessions, settings, runtime, llm)
        # Meanwhile three refunds were posted for this customer (e.g. by staff, manually).
        for i in range(3):
            await exec_sql(
                """INSERT INTO ledger_transactions (id, org_id, account_id, reference, kind, amount,
                       currency, merchant, status, reverses_id, occurred_at)
                   SELECT gen_random_uuid(), org_id, account_id, :ref, 'credit', 1, 'PKR', 'manual',
                       'posted', id, now()
                   FROM ledger_transactions WHERE reference = :orig""",
                {"ref": f"MANUAL-{i}-{uuid.uuid4().hex[:6]}", "orig": dup["reference"]},
            )
        [approval] = client.get("/v1/approvals", headers=owner.headers()).json()
        checker = make_admin(client, owner)
        client.post(f"/v1/approvals/{approval['id']}/approve", headers=checker.headers(), json={})
        await run(sessions, settings, runtime, llm)

        execution = workflow(client, owner, wf_id)["result"]["execution"]
        assert execution["status"] == "not_executed"
        assert "reversals in 24 hours" in execution["detail"]


async def test_refund_tool_is_exactly_once(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents()
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        dup = duplicate_of(client, owner, "CUST-1002")
        wf_id = uuid.UUID(submit(client, owner, "CUST-1002"))
        org_id = uuid.UUID(owner.org_id)

        async def execute() -> Any:
            async with sessions() as s, s.begin():
                await set_tenant_context(s, org_id=org_id)
                return await authorize_and_execute(
                    s,
                    org_id=org_id,
                    workflow_id=wf_id,
                    proposal=proposal(dup["reference"], dup["amount"]),
                    customer_ref="CUST-1002",
                    category="duplicate_charge",
                    needs_human_review=False,
                    knowledge_status="answered",
                    approval_status=None,
                )

        first, again = await execute(), await execute()  # e.g. a retry after a crash
        assert (first.status, first.replayed) == ("executed", False)
        assert (again.status, again.replayed) == ("executed", True)
        assert again.refund_reference == first.refund_reference
        assert await credits(sessions, owner.org_id) == 1
        async with sessions() as s, s.begin():
            await set_tenant_context(s, org_id=org_id)
            assert await s.scalar(select(func.count()).select_from(ActionExecution)) == 1


# --- Agents can't be talked into a bad action -----------------------------------------------


async def test_hallucinated_transaction_is_escalated_not_paid(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents("TXN-DOES-NOT-EXIST", "1200")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        wf_id = submit(client, owner, "CUST-1002")
        await run(sessions, settings, runtime, llm)
        r = workflow(client, owner, wf_id)["result"]
        assert r["proposal"]["action"] == "escalate_to_human"
        assert "not in the evidence" in r["proposal"]["rationale"]
        assert (r["decision"]["outcome"], r["execution"]["status"]) == ("block", "not_executed")
        assert await credits(sessions, owner.org_id) == 0


async def test_fraud_is_never_refunded_automatically(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="unauthorized_transaction")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        txns = client.get(
            "/v1/ledger/accounts/CUST-1003/transactions", headers=owner.headers()
        ).json()
        llm.script[4] = proposal(txns[0]["reference"], txns[0]["amount"])
        wf_id = submit(client, owner, "CUST-1003", "I never made these payments!")
        await run(sessions, settings, runtime, llm)
        r = workflow(client, owner, wf_id)["result"]
        assert r["proposal"]["action"] == "escalate_to_human"
        assert r["execution"]["status"] == "not_executed"
        assert await credits(sessions, owner.org_id) == 0


# --- Access control ------------------------------------------------------------------------


async def test_approvals_are_admin_only_and_tenant_scoped(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents()
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        dup = duplicate_of(client, owner, "CUST-1001")
        llm.script[4] = proposal(dup["reference"], dup["amount"])
        submit(client, owner, "CUST-1001")
        await run(sessions, settings, runtime, llm)
        [approval] = client.get("/v1/approvals", headers=owner.headers()).json()

        member = signup(client)
        client.post("/v1/members", headers=owner.headers(), json={"email": member.email})
        as_member = member.headers(org_id=owner.org_id)
        assert client.get("/v1/approvals", headers=as_member).status_code == 403
        assert (
            client.post(
                f"/v1/approvals/{approval['id']}/approve", headers=as_member, json={}
            ).status_code
            == 403
        )

        outsider = signup(client)  # owner (admin) of a different org
        assert client.get("/v1/approvals", headers=outsider.headers()).json() == []
        resp = client.post(
            f"/v1/approvals/{approval['id']}/approve", headers=outsider.headers(), json={}
        )
        assert resp.status_code == 404
        assert client.get("/v1/ledger/accounts", headers=outsider.headers()).json() == []


def test_demo_data_is_idempotent_and_admin_only(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        owner = signup(client)
        first = client.post("/v1/ledger/demo-data", headers=owner.headers()).json()
        again = client.post("/v1/ledger/demo-data", headers=owner.headers()).json()
        assert [a["customer_ref"] for a in first] == [f"CUST-100{i}" for i in range(1, 6)]
        assert first == again
        # The customer help guide (cheques, transfers, fraud) is queued once.
        [guide] = client.get("/v1/knowledge/documents", headers=owner.headers()).json()
        assert guide["title"] == "customer-help-guide"
        member = signup(client)
        client.post("/v1/members", headers=owner.headers(), json={"email": member.email})
        resp = client.post("/v1/ledger/demo-data", headers=member.headers(org_id=owner.org_id))
        assert resp.status_code == 403
