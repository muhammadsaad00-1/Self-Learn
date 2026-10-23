"""Dispute intelligence (is the customer's fraud claim supported by the records?) and the
business profile collected at signup."""

from typing import Any

from fastapi.testclient import TestClient

from agentflow.config import Settings
from agentflow.knowledge.runtime import KnowledgeRuntime
from agentflow.ledger.schemas import Evidence
from agentflow.policy import dispute
from agentflow.policy.engine import Outcome, evaluate
from tests.fakes import FakeLLM, proposal
from tests.helpers import signup
from tests.test_actions import Sessions, agents, run, setup_org
from tests.test_chat import issue, open_chat, say


def ledger(client: TestClient, headers: dict[str, str], ref: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = client.get(
        f"/v1/ledger/accounts/{ref}/transactions", headers=headers
    ).json()
    return result


def evidence_of(client: TestClient, headers: dict[str, str], ref: str) -> Evidence:
    return Evidence(
        customer_ref=ref,
        customer_name="x",
        account_found=True,
        card_status="active",
        transactions=ledger(client, headers, ref),
        duplicate_pairs=[],
        reversals_last_24h=0,
    )


def freeze(*refs: str) -> Any:
    return proposal(action="freeze_card").model_copy(update={"record_references": list(refs)})


# --- The assessment ---------------------------------------------------------------------------


def test_stranger_device_without_otp_is_likely_unauthorized(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        owner = signup(client)
        client.post("/v1/ledger/demo-data", headers=owner.headers())
        ev = evidence_of(client, owner.headers(), "CUST-1003")
        disputed = [t.reference for t in ev.transactions if t.merchant == "Unknown Merchant LLC"]

        result = dispute.assess(ev, disputed)
        assert result.verdict == "likely_unauthorized"
        assert {"unrecognised_device", "not_verified", "new_merchant"} <= {
            s.signal for s in result.signals
        }
        decision = evaluate(
            freeze(*disputed),
            evidence=ev,
            category="unauthorized_transaction",
            needs_human_review=False,
            knowledge_status=None,
            dispute_verdict=result.verdict,
        )
        assert decision.outcome == Outcome.ALLOW  # protect the customer at once


def test_own_phone_otp_and_regular_merchant_is_likely_customer_made(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        owner = signup(client)
        client.post("/v1/ledger/demo-data", headers=owner.headers())
        ev = evidence_of(client, owner.headers(), "CUST-1006")
        [big] = [t.reference for t in ev.transactions if t.amount == 18500]

        result = dispute.assess(ev, [big])
        assert result.verdict == "likely_customer_made"
        assert {"otp_verified", "registered_device", "known_merchant"} <= {
            s.signal for s in result.signals
        }
        decision = evaluate(
            freeze(big),
            evidence=ev,
            category="unauthorized_transaction",
            needs_human_review=False,
            knowledge_status=None,
            dispute_verdict=result.verdict,
        )
        assert (decision.outcome, decision.rules) == (Outcome.BLOCK, ["dispute_review"])
        assert dispute.assess(ev, []).verdict == "unclear"


async def test_false_fraud_claim_is_not_frozen_and_goes_to_review(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="unauthorized_transaction")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        [big] = [
            t for t in ledger(client, owner.headers(), "CUST-1006") if t["amount"] == "18500.00"
        ]
        llm.script[4] = freeze(big["reference"])
        customer = open_chat(client, owner, "CUST-1006")
        say(client, customer, "I never bought anything from Daraz for 18,500! Refund me.")
        await run(sessions, settings, runtime, llm)

        thread = issue(client, customer)
        card = thread["messages"][-1]["card"]
        assert "card_frozen" not in card  # not punished, not frozen
        assert card["records"][0]["reference"] == big["reference"]  # shown the facts
        accounts = client.get("/v1/ledger/accounts", headers=owner.headers()).json()
        assert (
            next(a for a in accounts if a["customer_ref"] == "CUST-1006")["card_status"] == "active"
        )

        [ticket] = client.get("/v1/tickets", headers=owner.headers()).json()
        assert any("Dispute check" in r for r in ticket["reasons"])
        reply_call = next(c for c in llm.calls if "case_number" in c["user"])
        assert "likely_customer_made" in reply_call["user"]  # the reply agent knows


async def test_genuine_fraud_claim_freezes_the_card(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="unauthorized_transaction")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        refs = [
            t["reference"]
            for t in ledger(client, owner.headers(), "CUST-1003")
            if t["merchant"] == "Unknown Merchant LLC"
        ]
        llm.script[4] = freeze(*refs)
        customer = open_chat(client, owner, "CUST-1003")
        say(client, customer, "85k gone to some merchant I've never heard of!")
        await run(sessions, settings, runtime, llm)

        assert issue(client, customer)["messages"][-1]["card"]["card_frozen"] is True
        wf = client.get("/v1/workflows", headers=owner.headers()).json()[0]
        result = client.get(f"/v1/workflows/{wf['id']}", headers=owner.headers()).json()["result"]
        assert result["dispute"]["verdict"] == "likely_unauthorized"


# --- Business profile ------------------------------------------------------------------------

PROFILE = {
    "industry": "microfinance_bank",
    "monthly_queries": 12000,
    "channels": ["app_chat", "email"],
    "languages": ["en", "ur", "roman_ur"],
    "minutes_per_query": 10,
    "agent_cost_per_hour": 1800,
}


def test_signup_records_the_business_profile(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        resp = client.post(
            "/v1/auth/signup",
            json={
                "email": "founder@example.com",
                "password": "correct horse battery staple",
                "full_name": "Sana Ali",
                "org_name": "Mehran Microfinance",
                "profile": PROFILE,
            },
        )
        assert resp.status_code == 201, resp.text
        headers = {
            "Authorization": f"Bearer {resp.json()['access_token']}",
            "X-Org-Id": resp.json()["org_id"],
        }
        saved = client.get("/v1/orgs/current/profile", headers=headers).json()
        assert saved["profile"]["industry"] == "microfinance_bank"
        assert saved["profile"]["currency"] == "PKR"  # defaults filled in

        changed = client.put(
            "/v1/orgs/current/profile", headers=headers, json=PROFILE | {"monthly_queries": 20000}
        )
        assert changed.json()["profile"]["monthly_queries"] == 20000
        bad = client.put(
            "/v1/orgs/current/profile",
            headers=headers,
            json=PROFILE | {"industry": "travel_agency"},
        )
        assert bad.status_code == 422  # built for companies that move money


def test_only_admins_change_the_profile(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        owner = signup(client)
        member = signup(client)
        client.post("/v1/members", headers=owner.headers(), json={"email": member.email})
        resp = client.put(
            "/v1/orgs/current/profile",
            headers=member.headers(org_id=owner.org_id),
            json=PROFILE,
        )
        assert resp.status_code == 403
        assert (
            client.get(
                "/v1/orgs/current/profile", headers=member.headers(org_id=owner.org_id)
            ).json()["profile"]
            is None
        )  # created before the wizard
