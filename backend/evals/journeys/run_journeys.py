"""Company journeys: the whole product, end to end, against the running stack and real models.

    make journeys            (needs `make up`, the worker, and GROQ_API_KEY; spends tokens)

What it does, as a bank would:
1. A new company signs up, loads its customers, and uploads its own policy document.
2. Customers write to the help chat (questions, account doubts, problems, a scam).
   Each journey checks the orchestration against expectations in journeys.jsonl:
   route (help chat or a new issue thread), issue status, the fact cards, ticket priority,
   and a key fact in the reply.
3. The team works the queue: approves the large refund (the issue then resolves itself),
   hands the fraud case to a colleague, who resolves it with a message to the customer.
4. Prints a report and the company's insights. Exit code 1 if any check failed.

Unlike pytest (fake models), this measures the real thing; add a journey for every
behaviour a demo depends on.
"""

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

API = "http://localhost:8000"
HERE = Path(__file__).parent
PASSWORD = "journeys-password-1"  # noqa: S105 (throwaway local test accounts)
TIMEOUT_S = 180


@dataclass
class Result:
    id: str
    route: str = "?"
    status: str | None = None
    seconds: float = 0.0
    reply: str = ""
    card: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


class Bank:
    """Staff session of one company, plus customer sessions on demand."""

    def __init__(self, client: httpx.Client) -> None:
        self.http = client
        email = f"ops-{uuid.uuid4().hex[:6]}@example.com"
        resp = client.post(
            "/v1/auth/signup",
            json={
                "email": email,
                "password": PASSWORD,
                "full_name": "Ayesha (Ops lead)",
                "org_name": "Mehran Microfinance Bank",
            },
        )
        resp.raise_for_status()
        self.email, self.org_id = email, resp.json()["org_id"]
        self.headers = {
            "Authorization": f"Bearer {resp.json()['access_token']}",
            "X-Org-Id": self.org_id,
        }
        self.customers: dict[str, dict[str, str]] = {}

    def get(self, path: str, **kw: Any) -> Any:
        resp = self.http.get(path, headers=self.headers, **kw)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        resp = self.http.post(path, headers=self.headers, json=body, **kw)
        resp.raise_for_status()
        return resp.json()

    def customer(self, ref: str) -> dict[str, str]:
        """Sign the customer in the way they would: phone + one-time code."""
        if ref not in self.customers:
            phone = f"9230012345{ref[-2:]}"
            start = self.http.post(
                "/v1/customer/login/start", json={"bank_id": self.org_id, "phone": phone}
            ).json()
            token = self.http.post(
                "/v1/customer/login/verify",
                json={"bank_id": self.org_id, "phone": phone, "code": start["dev_code"]},
            ).json()["customer_token"]
            self.customers[ref] = {"Authorization": f"Bearer {token}"}
        return self.customers[ref]

    def wait_for_workflows(self) -> None:
        """Until no workflow is queued or running (the worker is sequential)."""
        deadline = time.time() + TIMEOUT_S
        while time.time() < deadline:
            if all(w["status"] not in ("queued", "running") for w in self.get("/v1/workflows")):
                return
            time.sleep(1.5)
        raise TimeoutError("workflows still running")


def wait_until_ready(client: httpx.Client) -> None:
    """The API answers /ready once Postgres and Redis are reachable (e.g. after a restart)."""
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            if client.get("/ready").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(1)
    raise TimeoutError(f"{API} is not ready: run `make up`")


def setup(bank: Bank) -> None:
    print(f"Company: Mehran Microfinance Bank ({bank.email})")
    bank.post("/v1/ledger/demo-data")  # customers + the generic help guide
    doc = HERE / "mehran-microfinance-policies.md"
    bank.http.post(
        "/v1/knowledge/documents",
        headers=bank.headers,
        files={"file": (doc.name, doc.read_bytes(), "text/markdown")},
    ).raise_for_status()
    deadline = time.time() + TIMEOUT_S
    while (docs := bank.get("/v1/knowledge/documents")) and any(
        d["status"] != "ready" for d in docs
    ):
        if time.time() > deadline:
            raise TimeoutError("documents not ingested")
        time.sleep(2)
    print(f"Knowledge: {', '.join(d['title'] for d in docs)} (ready)\n")


def conversations(bank: Bank, customer: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = bank.http.get(
        "/v1/customer/conversations", headers=customer
    ).json()
    return result


def thread(bank: Bank, customer: dict[str, str], cid: str) -> dict[str, Any]:
    result: dict[str, Any] = bank.http.get(
        f"/v1/customer/conversations/{cid}", headers=customer
    ).json()
    return result


def run_journey(bank: Bank, journey: dict[str, Any]) -> Result:
    result = Result(journey["id"])
    customer = bank.customer(journey["customer"])
    before = {c["id"] for c in conversations(bank, customer)}
    help_id = conversations(bank, customer)[0]["id"]
    start = time.time()
    bank.http.post(
        f"/v1/customer/conversations/{help_id}/messages",
        headers=customer,
        json={"text": journey["message"]},
    ).raise_for_status()
    bank.wait_for_workflows()
    result.seconds = time.time() - start
    latest = bank.get("/v1/workflows")[0]
    if latest["status"] == "failed":  # e.g. the provider's rate limit: not a routing bug
        error = bank.get(f"/v1/workflows/{latest['id']}")["error"] or ""
        result.failures.append(f"workflow failed: {error[:120]}")

    new = [c for c in conversations(bank, customer) if c["id"] not in before]
    where = thread(bank, customer, new[0]["id"] if new else help_id)
    result.route, result.status = where["kind"], where["status"] if new else None
    last = next(m for m in reversed(where["messages"]) if m["role"] == "assistant")
    result.reply, result.card = last["body"], last["card"] or {}

    expect = journey["expect"]
    if result.route != expect["route"]:
        result.failures.append(f"route {result.route} != {expect['route']}")
    if "status" in expect and result.status != expect["status"]:
        result.failures.append(f"status {result.status} != {expect['status']}")
    result.failures += [f"no {k} card" for k in expect.get("card", []) if k not in result.card]
    result.failures += [
        f"unexpected {k} card" for k in expect.get("no_card", []) if k in result.card
    ]
    if (says := expect.get("says_any")) and not any(s in result.reply for s in says):
        result.failures.append(f"reply mentions none of {says}")
    if (priority := expect.get("priority")) and (
        result.card.get("case", {}).get("priority") != priority
    ):
        result.failures.append(f"case priority != {priority}")
    return result


def team_works_the_queue(bank: Bank) -> list[str]:
    """Approval resolves the in-review issue; a hand-off and a resolution close the fraud
    issue with a message the customer sees in it. Returns failures."""
    failures: list[str] = []
    print("\nTeam:")
    [approval] = bank.get("/v1/approvals", params={"status": "pending"})
    bank.post(f"/v1/approvals/{approval['id']}/approve", {"note": "Duplicate confirmed"})
    bank.wait_for_workflows()
    ayesha = bank.customer("CUST-1001")
    issue = next(c for c in conversations(bank, ayesha) if c["kind"] == "issue")
    print(f"  approved {approval['customer_ref']}'s refund → their issue is {issue['status']}")
    if issue["status"] != "resolved":
        failures.append(f"approved issue is {issue['status']}")

    colleague = f"fraud-{uuid.uuid4().hex[:6]}@example.com"
    bank.http.post(
        "/v1/auth/signup",
        json={
            "email": colleague,
            "password": PASSWORD,
            "full_name": "Sana (Fraud desk)",
            "org_name": "tmp",
        },
    ).raise_for_status()
    member = bank.post("/v1/members", {"email": colleague})
    ticket = next(t for t in bank.get("/v1/tickets") if t["customer_ref"] == "CUST-1003")
    bank.http.patch(
        f"/v1/tickets/{ticket['id']}",
        headers=bank.headers,
        json={"action": "assign", "assignee_id": member["user_id"], "note": "Fraud desk, yours"},
    ).raise_for_status()
    token = bank.http.post(
        "/v1/auth/login", data={"username": colleague, "password": PASSWORD}
    ).json()["access_token"]
    sana = {"Authorization": f"Bearer {token}", "X-Org-Id": bank.org_id}
    bank.http.patch(
        f"/v1/tickets/{ticket['id']}",
        headers=sana,
        json={
            "action": "resolve",
            "note": "Chargeback won with the card network",
            "customer_message": "Good news: the PKR 85,000 has been credited back and a new "
            "card is on its way.",
        },
    ).raise_for_status()
    hina = bank.customer("CUST-1003")
    issue = next(c for c in conversations(bank, hina) if c["kind"] == "issue")
    last = thread(bank, hina, issue["id"])["messages"][-1]
    print(f"  {ticket['code']} handed to Sana, resolved → Hina's issue is {issue['status']}")
    print(f'  Hina sees: "{last["body"]}"')
    if issue["status"] != "resolved" or last["role"] != "agent":
        failures.append("fraud issue not closed with the team's message")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", help="comma-separated journey ids (skips the team flow)")
    only = set(filter(None, (parser.parse_args().only or "").split(",")))
    journeys = [
        json.loads(line)
        for line in (HERE / "journeys.jsonl").read_text().splitlines()
        if line.strip()
    ]
    journeys = [j for j in journeys if not only or j["id"] in only]
    with httpx.Client(base_url=API, timeout=60) as client:
        wait_until_ready(client)
        bank = Bank(client)
        setup(bank)
        results = []
        for journey in journeys:
            r = run_journey(bank, journey)
            results.append(r)
            mark = "PASS" if not r.failures else "FAIL"
            where = r.route + (f" ({r.status})" if r.status else "")
            cards = ", ".join(r.card) or "-"
            print(f"{mark}  {r.id:<28} {where:<26} {r.seconds:5.1f}s  cards: {cards}")
            print(f"      ↳ {r.reply[:160]}")
            for f in r.failures:
                print(f"      ✗ {f}")
        team_failures = [] if only else team_works_the_queue(bank)
        stats = bank.get("/v1/insights")

    passed = sum(not r.failures for r in results)
    times = sorted(r.seconds for r in results)
    print(
        f"\n{passed}/{len(results)} journeys passed · team flow "
        + ("skipped (--only)" if only else "passed" if not team_failures else "FAILED: ")
        + "; ".join(team_failures)
    )
    money, cost = float(stats["money_returned"]), float(stats["ai_cost_usd"])
    per_request = float(stats["ai_cost_per_request_usd"])
    print(
        f"Latency: median {times[len(times) // 2]:.0f}s, max {times[-1]:.0f}s · "
        f"AI resolved {stats['resolved_by_ai']}/{stats['completed']} completed "
        f"({stats['ai_resolution_rate']:.0%}) · returned PKR {money:,.0f} · "
        f"AI cost ${cost:.4f} (${per_request:.5f}/request)"
    )
    print(f"Log in as {bank.email} / {PASSWORD} to see it in the dashboard.")
    return 0 if passed == len(results) and not team_failures else 1


if __name__ == "__main__":
    sys.exit(main())
