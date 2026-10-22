"""The help-chat / issue-thread orchestration (real database, fake models).

Questions stay in the help chat; problems get their own issue thread with a status that
follows the outcome (resolved, in review, with a specialist) and the ticket's life."""

import uuid
from typing import Any

from sqlalchemy import func, select

from agentflow.config import Settings
from agentflow.conversations import service as conversations_service
from agentflow.conversations.models import Conversation, ConversationKind
from agentflow.db import set_tenant_context
from agentflow.knowledge.runtime import KnowledgeRuntime
from agentflow.llm import LLMError
from agentflow.workflow.models import Workflow
from tests.fakes import FakeLLM, proposal, reply
from tests.test_actions import Sessions, agents, run, setup_org
from tests.test_chat import chat, conversations, issue, open_chat, say


async def test_reported_issue_keeps_its_thread_and_reopens(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="failed_payment")
    llm.script[4] = proposal(action="no_action")  # nothing to fix: resolved with a reply
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        customer = open_chat(client, owner, "CUST-1005")
        resp = client.post(
            "/v1/customer/issues", headers=customer, json={"text": "My 15000 IBFT is stuck"}
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert (created["kind"], created["status"], created["thinking"]) == ("issue", "open", True)
        await run(sessions, settings, runtime, llm)

        thread = chat(client, customer, created["id"])
        assert thread["status"] == "resolved"
        assert thread["subject"] == "Charged twice for a transfer"  # triage's title
        assert [m["role"] for m in thread["messages"]] == ["customer", "assistant"]

        # A follow-up in the issue stays in the issue (no second issue), with context,
        # and reopens it until the agents answer again.
        say(client, customer, "still not received!", created["id"])
        assert chat(client, customer, created["id"])["status"] == "open"
        await run(sessions, settings, runtime, llm)
        assert [c["kind"] for c in conversations(client, customer)] == ["help", "issue"]
        follow_up = next(c["user"] for c in llm.calls if "still not received" in c["user"])
        assert "customer: My 15000 IBFT is stuck" in follow_up
        assert len(chat(client, customer, created["id"])["messages"]) == 4


async def test_small_talk_never_opens_an_issue_or_ticket(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="other")
    llm.script[5] = reply("You're welcome!")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        customer = open_chat(client, owner, "CUST-1002")
        say(client, customer, "thanks a lot!")
        await run(sessions, settings, runtime, llm)

        assert [c["kind"] for c in conversations(client, customer)] == ["help"]
        assert chat(client, customer)["messages"][-1]["body"] == "You're welcome!"
        assert client.get("/v1/tickets?status=all", headers=owner.headers()).json() == []


async def test_customers_only_see_their_own_conversations(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    with client_for(agents()) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        ayesha = open_chat(client, owner, "CUST-1001")
        bilal = open_chat(client, owner, "CUST-1002")
        theirs = client.post("/v1/customer/issues", headers=ayesha, json={"text": "help"}).json()

        url = f"/v1/customer/conversations/{theirs['id']}"
        assert client.get(url, headers=bilal).status_code == 404
        resp = client.post(f"{url}/messages", headers=bilal, json={"text": "hi"})
        assert resp.status_code == 404
        assert [c["kind"] for c in conversations(client, bilal)] == ["help"]


async def test_routing_is_idempotent_for_retried_workflows(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    with client_for(agents()) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        customer = open_chat(client, owner, "CUST-1001")
        say(client, customer, "charged twice!")
        org_id = uuid.UUID(owner.org_id)
        async with sessions() as s, s.begin():
            await set_tenant_context(s, org_id=org_id)
            wf_id = await s.scalar(select(Workflow.id).where(Workflow.org_id == org_id))
            assert wf_id is not None
            triage = {"category": "duplicate_charge", "summary": "Charged twice"}
            for _ in range(2):  # e.g. the worker crashed after triage and the job was retried
                await conversations_service.after_triage(s, wf_id, triage)
            issues = await s.scalar(
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.kind == ConversationKind.ISSUE)
            )
        assert issues == 1
        assert len(chat(client, customer)["messages"]) == 2  # one "opened an issue" message


async def test_issue_status_follows_its_ticket(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = agents(category="unauthorized_transaction")
    llm.script[4] = proposal(action="freeze_card")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        customer = open_chat(client, owner, "CUST-1003")
        say(client, customer, "I didn't make these payments")
        await run(sessions, settings, runtime, llm)
        assert issue(client, customer)["status"] == "with_specialist"

        [ticket] = client.get("/v1/tickets", headers=owner.headers()).json()
        url = f"/v1/tickets/{ticket['id']}"
        client.patch(url, headers=owner.headers(), json={"action": "resolve", "note": "done"})
        resolved = issue(client, customer)
        assert resolved["status"] == "resolved"
        assert resolved["messages"][-1]["role"] == "assistant"  # no customer message: silent

        client.patch(url, headers=owner.headers(), json={"action": "reopen"})
        assert issue(client, customer)["status"] == "with_specialist"


async def test_answered_question_without_a_tool_is_not_escalated(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    """Real-model finding: "what documents do I need for a loan?" was triaged kyc_document,
    escalated (no KYC tool) and ticketed although the documents answered it."""
    llm = agents(category="kyc_document")
    with client_for(llm) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        customer = open_chat(client, owner, "CUST-1002")
        say(client, customer, "What documents do I need for a business loan?")
        await run(sessions, settings, runtime, llm)

        assert [c["kind"] for c in conversations(client, customer)] == ["help"]
        assert "sources" in chat(client, customer)["messages"][-1]["card"]
        assert client.get("/v1/tickets?status=all", headers=owner.headers()).json() == []


async def test_provider_outage_hands_the_customer_to_a_human(
    client_for: Any,
    settings: Settings,
    sessions: Sessions,
    runtime: KnowledgeRuntime,
    exec_sql: Any,
) -> None:
    """Real-model finding: with Groq's daily limit exhausted, workflows dead-lettered and
    customers were promised a specialist with no ticket behind the promise."""
    down = FakeLLM(LLMError("rate limited"))
    with client_for(down) as client:
        owner = await setup_org(client, sessions, settings, runtime)
        customer = open_chat(client, owner, "CUST-1002")
        say(client, customer, "my card isn't working")
        for _ in range(settings.workflow_max_attempts):
            await exec_sql("UPDATE workflows SET available_at = now()", {})
            assert await run(sessions, settings, runtime, down)

        [ticket] = client.get("/v1/tickets", headers=owner.headers()).json()
        last = chat(client, customer)["messages"][-1]
        assert ticket["code"] in last["body"]
        assert last["card"]["case"]["code"] == ticket["code"]
