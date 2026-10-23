"""The email channel, the ROI numbers, and the wall between the customer app and the team's
dashboard."""

import re
import uuid
from typing import Any

from agentflow.config import Settings
from agentflow.inbound.api import inbound_token
from agentflow.knowledge.runtime import KnowledgeRuntime
from tests.fakes import FakeLLM
from tests.helpers import signup
from tests.test_actions import Sessions, agents, run, setup_org
from tests.test_chat import open_chat


def test_inbound_email_becomes_a_request_matched_to_the_customer(
    client_for: Any, settings: Settings
) -> None:
    with client_for(FakeLLM()) as client:
        owner = signup(client)
        client.post("/v1/ledger/demo-data", headers=owner.headers())
        address = client.get("/v1/inbound/address", headers=owner.headers()).json()["url"]
        path = address.split("testserver")[1]
        email = {
            "from_address": "CUST1004@example.com",  # case doesn't matter
            "subject": "Transfer not received",
            "text": "I sent 3000 to Sara yesterday.",
            "message_id": "<abc@mail>",
        }
        first = client.post(path, json=email)
        assert first.status_code == 202, first.text
        assert first.json()["customer_ref"] == "CUST-1004"
        again = client.post(path, json=email)  # the provider retried: same request
        assert again.json()["workflow_id"] == first.json()["workflow_id"]

        [request] = client.get("/v1/workflows", headers=owner.headers()).json()
        assert (request["channel"], request["customer_ref"]) == ("email", "CUST-1004")
        assert request["message_preview"].startswith("Transfer not received")

        stranger = client.post(
            path, json=email | {"from_address": "who@example.com", "message_id": "x"}
        )
        assert stranger.json()["customer_ref"] is None  # still answered from the documents

        wrong = f"/v1/inbound/email/{owner.org_id}/{'0' * 32}"
        assert client.post(wrong, json=email).status_code == 404
        other_org = (
            f"/v1/inbound/email/{uuid.uuid4()}/{inbound_token(uuid.UUID(owner.org_id), settings)}"
        )
        assert client.post(other_org, json=email).status_code == 404


def test_sample_inbox_and_address_are_admin_only(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        owner = signup(client)
        client.post("/v1/ledger/demo-data", headers=owner.headers())
        loaded = client.post("/v1/inbound/sample", headers=owner.headers()).json()
        assert len(loaded) == 8
        assert sum(1 for a in loaded if a["customer_ref"]) == 7  # one unknown sender
        member = signup(client)
        client.post("/v1/members", headers=owner.headers(), json={"email": member.email})
        as_member = member.headers(org_id=owner.org_id)
        assert client.get("/v1/inbound/address", headers=as_member).status_code == 403
        assert client.post("/v1/inbound/sample", headers=as_member).status_code == 403


async def test_overview_shows_the_business_case(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="general_inquiry")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        client.put(
            "/v1/orgs/current/profile",
            headers=owner.headers(),
            json={
                "industry": "digital_wallet",
                "monthly_queries": 30000,
                "minutes_per_query": 6,
                "agent_cost_per_hour": 2000,
            },
        )
        client.post(
            "/v1/workflows",
            headers={**owner.headers(), "Idempotency-Key": uuid.uuid4().hex},
            json={"message": "How do I issue a cheque?", "customer_ref": "CUST-1002"},
        )
        await run(sessions, settings, runtime, llm)

        stats = client.get("/v1/insights", headers=owner.headers()).json()
        assert stats["resolved_by_ai"] == 1
        assert stats["team_hours_saved"] == 0.1  # 6 minutes
        assert stats["team_cost_saved"] == "200"  # 0.1 h x 2,000
        assert stats["projected_monthly_hours_saved"] == 3000  # 30,000 x 100% x 6 min
        assert "30,000 queries a month" in stats["assumptions"]


# --- The customer app never reaches the team's side -------------------------------------------

PUBLIC = ("/v1/auth/", "/v1/customer/login", "/v1/inbound/email/")


def test_a_customer_token_opens_no_team_endpoint(client_for: Any) -> None:
    """Every staff API route refuses a customer's token: the customer app is a separate
    product, not a restricted view of the dashboard."""
    with client_for(FakeLLM()) as client:
        owner = signup(client)
        client.post("/v1/ledger/demo-data", headers=owner.headers())
        customer = open_chat(client, owner, "CUST-1001") | {"X-Org-Id": owner.org_id}

        checked = 0
        # The OpenAPI schema lists every endpoint (included routers are nested in app.routes).
        for path, operations in client.app.openapi()["paths"].items():
            if not path.startswith("/v1/") or path.startswith(("/v1/customer/", *PUBLIC)):
                continue
            concrete = re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)
            for method in operations:
                resp = client.request(method.upper(), concrete, headers=customer, json={})
                assert resp.status_code in (401, 403), (method, path, resp.status_code)
                checked += 1
        assert checked > 30


def test_customer_app_is_branded_with_the_bank(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        owner = signup(client)
        client.post("/v1/ledger/demo-data", headers=owner.headers())
        name = client.get(f"/v1/customer/bank/{owner.org_id}").json()["name"]
        assert name == "Acme Pay"
        assert client.get(f"/v1/customer/bank/{uuid.uuid4()}").status_code == 404
        me = client.get("/v1/customer/me", headers=open_chat(client, owner, "CUST-1001")).json()
        assert me["bank_name"] == name
