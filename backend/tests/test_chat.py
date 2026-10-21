"""Customer chat end to end (real database, fake models): a customer message runs the agents,
the outcome comes back as a chat message with a card; failed transfers are reversed at once,
fraud freezes the card; staff can take over; insights add it all up."""

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentflow.agents.resolution import validate
from agentflow.config import Settings
from agentflow.knowledge.runtime import KnowledgeRuntime
from agentflow.ledger.schemas import Evidence, TransactionOut
from agentflow.policy.engine import Outcome, evaluate
from tests.fakes import FakeLLM, proposal
from tests.helpers import Account, signup
from tests.test_actions import Sessions, agents, run, setup_org


def open_chat(client: TestClient, owner: Account, customer_ref: str) -> dict[str, str]:
    resp = client.post(
        "/v1/conversations", headers=owner.headers(), json={"customer_ref": customer_ref}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['customer_token']}"}


def say(client: TestClient, customer: dict[str, str], text: str) -> None:
    resp = client.post("/v1/chat/messages", headers=customer, json={"text": text})
    assert resp.status_code == 201, resp.text


def chat(client: TestClient, customer: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = client.get("/v1/chat", headers=customer).json()
    return result


def transfer_of(
    client: TestClient, owner: Account, customer_ref: str, status: str
) -> dict[str, Any]:
    txns = client.get(f"/v1/ledger/accounts/{customer_ref}/transactions", headers=owner.headers())
    found: dict[str, Any] = next(t for t in txns.json() if t["transfer_status"] == status)
    return found


# --- Failed transfers and card freeze (pure rules) -------------------------------------------


def evidence(transfer_status: str | None, card_status: str = "active") -> Evidence:
    txn = TransactionOut.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "reference": "TXN-1",
            "kind": "debit",
            "amount": "5000.00",
            "currency": "PKR",
            "merchant": "IBFT to Ali",
            "status": "posted",
            "transfer_status": transfer_status,
            "occurred_at": "2026-10-21T10:00:00Z",
        }
    )
    return Evidence(
        customer_ref="C",
        customer_name="N",
        account_found=True,
        card_status=card_status,
        transactions=[txn],
        duplicate_pairs=[],
        reversals_last_24h=0,
    )


def test_network_failed_transfer_is_reversed_without_approval() -> None:
    ev = evidence("failed")
    p = validate(proposal("TXN-1", "5000"), ev, "failed_payment")
    decision = evaluate(
        p, evidence=ev, category="failed_payment", needs_human_review=False, knowledge_status=None
    )
    assert (decision.outcome, decision.rules) == (Outcome.ALLOW, ["failed_transfer_reversal"])


@pytest.mark.parametrize("transfer_status", ["pending", "credited", None])
def test_only_network_failed_transfers_are_reversed(transfer_status: str | None) -> None:
    p = validate(proposal("TXN-1", "5000"), evidence(transfer_status), "failed_payment")
    assert p.action == "escalate_to_human"
    assert "not a transfer the network reports as failed" in p.rationale


def test_card_freeze_only_for_fraud_and_active_cards() -> None:
    freeze = proposal(action="freeze_card")
    ok = validate(freeze, evidence(None), "unauthorized_transaction")
    assert ok.action == "freeze_card"
    assert (
        evaluate(
            ok,
            evidence=evidence(None),
            category="unauthorized_transaction",
            needs_human_review=True,
            knowledge_status=None,
        ).outcome
        == Outcome.ALLOW
    )
    assert validate(freeze, evidence(None), "duplicate_charge").action == "escalate_to_human"
    assert validate(freeze, evidence(None, "frozen"), "unauthorized_transaction").action == (
        "escalate_to_human"
    )


# --- End to end through the chat ---------------------------------------------------------------


async def test_failed_transfer_is_reversed_in_the_chat(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="failed_payment")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        failed = transfer_of(client, owner, "CUST-1004", "failed")
        llm.script[4] = proposal(failed["reference"], failed["amount"])
        customer = open_chat(client, owner, "CUST-1004")

        say(client, customer, "maine Ali ko 5000 bheje, kat gaye lekin usko nahi mile")
        assert chat(client, customer)["thinking"] is True
        await run(sessions, settings, runtime, llm)

        convo = chat(client, customer)
        assert convo["thinking"] is False
        assert convo["customer_name"] == "Usman Tariq"
        mine, answer = convo["messages"]
        assert (mine["role"], answer["role"]) == ("customer", "assistant")
        card = answer["card"]
        assert Decimal(card["refund"]["amount"]) == Decimal("5000")  # no approval needed
        assert card["transfer"]["network_status"] == "failed"
        assert card["transfer"]["reversed"] is True
        assert "case" not in card


async def test_fraud_freezes_the_card_and_opens_a_case(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="unauthorized_transaction", urgency="critical")
    llm.script[4] = proposal(action="freeze_card")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        customer = open_chat(client, owner, "CUST-1003")
        say(client, customer, "I never made these 85k payments!!")
        await run(sessions, settings, runtime, llm)

        card = chat(client, customer)["messages"][-1]["card"]
        assert card["card_frozen"] is True
        assert card["case"]["code"].startswith("TCK-")
        [account] = [
            a
            for a in client.get("/v1/ledger/accounts", headers=owner.headers()).json()
            if a["customer_ref"] == "CUST-1003"
        ]
        assert account["card_status"] == "frozen"


async def test_large_refund_tells_the_customer_it_is_under_review(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents()
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        txns = client.get("/v1/ledger/accounts/CUST-1001/transactions", headers=owner.headers())
        dup = sorted(
            (t for t in txns.json() if t["merchant"] == "KE-Bills"), key=lambda t: t["occurred_at"]
        )[1]
        llm.script[4] = proposal(dup["reference"], dup["amount"])
        customer = open_chat(client, owner, "CUST-1001")
        say(client, customer, "My 5000 bill was charged twice")
        await run(sessions, settings, runtime, llm)

        answer = chat(client, customer)["messages"][-1]
        assert "second officer" in answer["body"]
        assert answer["card"]["review"]["amount"] == "5000.00"


async def test_follow_ups_carry_the_conversation_as_context(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="general_inquiry")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        customer = open_chat(client, owner, "CUST-1002")
        say(client, customer, "how do I issue a cheque?")
        await run(sessions, settings, runtime, llm)
        say(client, customer, "and how long is it valid?")
        await run(sessions, settings, runtime, llm)

        last_triage = next(c["user"] for c in llm.calls if "how long is it valid" in c["user"])
        assert "customer: how do I issue a cheque?" in last_triage
        assert len(chat(client, customer)["messages"]) == 4


# --- Staff side ------------------------------------------------------------------------------


async def test_staff_take_over_and_hand_back(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="general_inquiry")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        customer = open_chat(client, owner, "CUST-1002")
        [convo] = client.get("/v1/conversations", headers=owner.headers()).json()
        url = f"/v1/conversations/{convo['id']}"

        resp = client.post(
            f"{url}/messages", headers=owner.headers(), json={"text": "Hi, Sana here"}
        )
        assert resp.status_code == 201
        say(client, customer, "thanks Sana")
        assert await run(sessions, settings, runtime, llm) is False  # agents stay quiet
        state = chat(client, customer)
        assert state["mode"] == "human"
        assert state["thinking"] is False
        assert [m["role"] for m in state["messages"]] == ["agent", "customer"]

        assert client.post(f"{url}/handback", headers=owner.headers()).json()["mode"] == "ai"
        say(client, customer, "what are IBFT charges?")
        assert await run(sessions, settings, runtime, llm) is True

        listed = client.get("/v1/conversations", headers=owner.headers()).json()
        assert listed[0]["last_message"]["role"] == "assistant"
        outsider = signup(client)
        assert client.get(url, headers=outsider.headers()).status_code == 404


def test_customer_and_staff_tokens_never_mix(client_for: Any, settings: Settings) -> None:
    with client_for(FakeLLM()) as client:
        owner = signup(client)
        client.post("/v1/ledger/demo-data", headers=owner.headers())
        customer = open_chat(client, owner, "CUST-1001")
        assert client.get("/v1/chat", headers=owner.headers()).status_code == 401
        staff_with_customer_token = {**customer, "X-Org-Id": owner.org_id}
        assert client.get("/v1/conversations", headers=staff_with_customer_token).status_code == 401
        assert client.get("/v1/chat", headers={"Authorization": "Bearer junk"}).status_code == 401
        assert client.get("/v1/chat").status_code == 401
        resp = client.post(
            "/v1/conversations", headers=owner.headers(), json={"customer_ref": "NOPE"}
        )
        assert resp.status_code == 404


async def test_voice_note_is_transcribed_into_the_chat(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="general_inquiry")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        heard: list[tuple[int, str]] = []

        async def transcribe(data: bytes, filename: str) -> str:
            heard.append((len(data), filename))
            return "cheque book kaise milegi?"

        client.app.state.transcriber = transcribe
        customer = open_chat(client, owner, "CUST-1002")
        resp = client.post(
            "/v1/chat/voice",
            headers=customer,
            files={"file": ("note.webm", b"\x1a" * 99, "audio/webm")},
        )
        assert resp.status_code == 201, resp.text
        assert (resp.json()["body"], resp.json()["card"]) == (
            "cheque book kaise milegi?",
            {"voice": True},
        )
        assert heard == [(99, "note.webm")]
        await run(sessions, settings, runtime, llm)
        assert chat(client, customer)["messages"][-1]["role"] == "assistant"


async def test_insights_add_up(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="failed_payment")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        failed = transfer_of(client, owner, "CUST-1004", "failed")
        llm.script[4] = proposal(failed["reference"], failed["amount"])
        say(client, open_chat(client, owner, "CUST-1004"), "transfer didn't reach")
        await run(sessions, settings, runtime, llm)

        stats = client.get("/v1/insights", headers=owner.headers()).json()
        assert (stats["requests"], stats["completed"], stats["resolved_by_ai"]) == (1, 1, 1)
        assert stats["ai_resolution_rate"] == 1.0
        assert Decimal(stats["money_returned"]) == Decimal("5000")
        assert stats["by_channel"] == {"chat": 1}
        assert stats["by_category"] == {"failed_payment": 1}
        assert Decimal(stats["ai_cost_usd"]) >= 0
