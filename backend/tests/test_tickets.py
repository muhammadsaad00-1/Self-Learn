"""Tickets: opened automatically for critical outcomes, exactly once; the team's queue API."""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from agentflow.config import Settings
from agentflow.db import set_tenant_context
from agentflow.knowledge.runtime import KnowledgeRuntime
from agentflow.policy.engine import Outcome, PolicyDecision, ticket_reasons
from agentflow.tickets.models import Ticket
from agentflow.tools.tickets import open_ticket
from tests.fakes import FakeLLM, proposal
from tests.helpers import signup
from tests.test_actions import (
    Sessions,
    agents,
    duplicate_of,
    make_admin,
    run,
    setup_org,
    submit,
    workflow,
)


def tickets(
    client: TestClient, headers: dict[str, str], status: str = "open"
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = client.get(
        f"/v1/tickets?status={status}", headers=headers
    ).json()
    return result


# --- When a ticket is opened (pure rule) ---------------------------------------------------

BLOCKED = PolicyDecision(
    outcome=Outcome.BLOCK, reasons=["Escalated to a human"], rules=["escalation"]
)
ALLOWED = PolicyDecision(outcome=Outcome.ALLOW, reasons=["ok"], rules=["auto_approve"])


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"category": "unauthorized_transaction"}, "Suspected fraud"),
        ({"urgency": "critical"}, "Critical urgency"),
        ({"decision": BLOCKED}, "Not automated: Escalated to a human"),
        ({"approval_status": "rejected"}, "Approval rejected"),
        ({"approval_status": "expired"}, "Approval expired"),
        (
            {
                "approval_status": "approved",
                "execution_status": "not_executed",
                "execution_detail": "limit",
            },
            "could not be executed: limit",
        ),
    ],
)
def test_critical_outcomes_get_a_ticket(kwargs: dict[str, Any], expected: str) -> None:
    base: dict[str, Any] = {
        "category": "duplicate_charge",
        "urgency": "normal",
        "decision": ALLOWED,
        "approval_status": None,
        "execution_status": "executed",
        "execution_detail": None,
    }
    reasons = ticket_reasons(**(base | kwargs))
    assert any(expected in r for r in reasons), reasons


def test_routine_outcomes_need_no_ticket() -> None:
    assert (
        ticket_reasons(
            category="duplicate_charge",
            urgency="high",
            decision=ALLOWED,
            approval_status="approved",
            execution_status="executed",
            execution_detail=None,
        )
        == []
    )


# --- End to end ------------------------------------------------------------------------------


async def test_fraud_opens_a_ticket_with_the_case_number_in_the_reply(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="unauthorized_transaction", urgency="critical")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        txns = client.get(
            "/v1/ledger/accounts/CUST-1003/transactions", headers=owner.headers()
        ).json()
        llm.script[4] = proposal(txns[0]["reference"], txns[0]["amount"])
        wf_id = submit(client, owner, "CUST-1003", "Someone stole 85,000 from my wallet!")
        await run(sessions, settings, runtime, llm)

        r = workflow(client, owner, wf_id)["result"]
        assert r["execution"]["status"] == "not_executed"
        ticket = r["ticket"]
        assert ticket["code"].startswith("TCK-")
        assert ticket["priority"] == "critical"
        assert any("Suspected fraud" in reason for reason in ticket["reasons"])
        # The communication agent was told the case number.
        reply_call = next(c for c in llm.calls if "case_number" in c["user"])
        assert ticket["code"] in reply_call["user"]

        [queued] = tickets(client, owner.headers())
        assert (queued["code"], queued["customer_ref"], queued["workflow_id"]) == (
            ticket["code"],
            "CUST-1003",
            wf_id,
        )
        assert "Someone stole 85,000" in queued["description"]


async def test_rejected_approval_opens_a_ticket(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents()
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        dup = duplicate_of(client, owner, "CUST-1001")
        llm.script[4] = proposal(dup["reference"], dup["amount"])
        wf_id = submit(client, owner, "CUST-1001")
        await run(sessions, settings, runtime, llm)
        assert tickets(client, owner.headers()) == []  # waiting for approval: inbox owns it
        [approval] = client.get("/v1/approvals", headers=owner.headers()).json()
        checker = make_admin(client, owner)
        client.post(f"/v1/approvals/{approval['id']}/reject", headers=checker.headers(), json={})
        await run(sessions, settings, runtime, llm)
        ticket = workflow(client, owner, wf_id)["result"]["ticket"]
        assert any("Approval rejected" in reason for reason in ticket["reasons"])


async def test_ticket_tool_is_exactly_once(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    with client_for(FakeLLM()) as client:
        owner = signup(client)
        wf_id = uuid.UUID(submit(client, owner, "CUST-X"))
        org_id = uuid.UUID(owner.org_id)

        async def open_once() -> Any:
            async with sessions() as s, s.begin():
                await set_tenant_context(s, org_id=org_id)
                return await open_ticket(
                    s,
                    org_id=org_id,
                    workflow_id=wf_id,
                    reasons=["Critical urgency"],
                    state={"message": "m"},
                )

        first, again = await open_once(), await open_once()  # e.g. a retried workflow
        assert first.code == again.code
        async with sessions() as s, s.begin():
            await set_tenant_context(s, org_id=org_id)
            assert await s.scalar(select(func.count()).select_from(Ticket)) == 1


# --- Queue API ------------------------------------------------------------------------------


async def test_team_takes_and_resolves_tickets(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="unauthorized_transaction")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        submit(client, owner, "CUST-1003", "fraud!")
        await run(sessions, settings, runtime, llm)
        [ticket] = tickets(client, owner.headers())

        member = signup(client)  # plain members work tickets too
        client.post("/v1/members", headers=owner.headers(), json={"email": member.email})
        as_member = member.headers(org_id=owner.org_id)
        url = f"/v1/tickets/{ticket['id']}"
        taken = client.patch(url, headers=as_member, json={"action": "take"}).json()
        assert (taken["status"], taken["assignee_id"]) == ("in_progress", member.user_id)
        assert client.patch(url, headers=as_member, json={"action": "resolve"}).status_code == 422
        done = client.patch(
            url, headers=as_member, json={"action": "resolve", "note": "Card blocked, refund filed"}
        )
        assert done.json()["status"] == "resolved"
        assert tickets(client, as_member) == []
        assert len(tickets(client, as_member, "resolved")) == 1

        outsider = signup(client)
        assert (
            client.patch(url, headers=outsider.headers(), json={"action": "take"}).status_code
            == 404
        )
        assert tickets(client, outsider.headers(), "all") == []
        events = client.get("/v1/audit/events", headers=owner.headers()).json()["events"]
        assert [e["action"] for e in events][:2] == ["ticket.resolve", "ticket.take"]
