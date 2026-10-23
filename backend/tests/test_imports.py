"""Data imports: typed / CSV / report rows are checked, reviewed by a person, and committed to
the real tables once. Industry decides the record types (banks: customers + transactions;
transport: customers + trips), and the agents answer from what was imported."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from agentflow.agents.extraction import schema_for
from agentflow.config import Settings
from agentflow.datasets.templates import CUSTOMERS, TRANSACTIONS, TRIPS, check_row, map_headers
from agentflow.knowledge.runtime import KnowledgeRuntime
from agentflow.worker import process_next_import
from tests.fakes import FakeLLM, proposal, query_plan, reply, triage_result
from tests.helpers import PASSWORD, Account, signup
from tests.test_actions import Sessions, run
from tests.test_chat import chat, open_chat, say

# --- Templates and cleaning (pure) ------------------------------------------------------------


def test_real_world_headers_map_to_fields() -> None:
    headers = ["Txn Ref", "Customer ID", "Value Date", "Narration", "Amount (PKR)", "Dr/Cr", "Foo"]
    mapping = map_headers(TRANSACTIONS, headers)
    assert [mapping.get(i) for i in range(len(headers))] == [
        "reference",
        "customer_ref",
        "occurred_at",
        "description",
        "amount",
        "direction",
        None,  # unknown column: ignored, and reported
    ]


@pytest.mark.parametrize(
    ("raw", "field", "expected"),
    [
        ({"amount": "PKR 95,000"}, "amount", "95000.00"),
        ({"amount": "(3,499.00)"}, "amount", "3499.00"),  # statement brackets: a debit
        ({"direction": "Dr"}, "direction", "debit"),
        ({"direction": "CR"}, "direction", "credit"),
        ({"occurred_at": "21/10/2026 14:30"}, "occurred_at", "2026-10-21T14:30:00+00:00"),
        ({"occurred_at": "21 Oct 2026"}, "occurred_at", "2026-10-21T00:00:00+00:00"),
        ({"verified": "Yes"}, "verified", True),
    ],
)
def test_cells_are_cleaned(raw: dict[str, str], field: str, expected: Any) -> None:
    assert check_row(TRANSACTIONS, raw).data[field] == expected


def test_times_without_a_zone_are_the_companys_local_time() -> None:
    """QA: a Karachi operator's "17:00" was read as UTC and announced as "17:00 UTC"."""
    row = {
        "trip_ref": "T",
        "origin": "A",
        "destination": "B",
        "departs_at": "2026-11-01 17:00",
        "fare": "100",
        "seats_available": "5",
    }
    checked = check_row(TRIPS, row, ZoneInfo("Asia/Karachi"))
    departs = datetime.fromisoformat(checked.data["departs_at"])
    assert departs.astimezone(UTC).hour == 12  # 17:00 PKT = 12:00 UTC
    assert (
        check_row(TRIPS, row | {"departs_at": "2026-11-01T17:00Z"})
        .data["departs_at"]
        .endswith("+00:00")
    )  # an explicit zone wins


def test_problems_are_named_per_field() -> None:
    bad = check_row(CUSTOMERS, {"customer_ref": "C1", "phone": "0300-12", "email": "a@b"})
    assert bad.errors == {
        "customer_name": "required",
        "phone": "not a mobile number",
        "email": "not an email address",
    }
    negative = check_row(TRANSACTIONS, {"amount": "(500)"})
    assert (negative.data["direction"], "direction" in negative.errors) == ("debit", False)
    seats = check_row(
        TRIPS,
        {
            "trip_ref": "T",
            "origin": "A",
            "destination": "B",
            "departs_at": "2026-11-01 10:00",
            "fare": "100",
            "seats_available": "50",
            "seats_total": "45",
        },
    )
    assert seats.errors == {"seats_available": "more than the total seats"}


# --- Bank: review, fix, commit ----------------------------------------------------------------


def bank(client: TestClient, industry: str = "commercial_bank") -> Account:
    email = f"imp-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/v1/auth/signup",
        json={
            "email": email,
            "password": PASSWORD,
            "full_name": "Data Lead",
            "org_name": "Import Co",
            "profile": {"industry": industry},
        },
    )
    j = resp.json()
    return Account(email, j["access_token"], j["user"]["id"], j["org_id"])


def test_customers_are_reviewed_fixed_then_committed_once(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        owner = bank(client)
        h = owner.headers()
        batch = client.post(
            "/v1/imports/sample", headers=h, json={"record_type": "customers"}
        ).json()
        assert (batch["status"], batch["row_count"], batch["error_count"]) == ("review", 4, 1)
        bad = next(r for r in batch["rows"] if r["errors"])
        assert set(bad["errors"]) == {"phone", "email"}

        url = f"/v1/imports/{batch['id']}"
        blocked = client.post(f"{url}/commit", headers=h).json()
        assert blocked == {
            "committed": False,
            "blocked": 1,
            "created": 0,
            "updated": 0,
            "skipped_duplicates": 0,
        }
        assert client.get("/v1/ledger/accounts", headers=h).json() == []  # nothing written

        fixed = client.patch(
            f"{url}/rows/{bad['id']}",
            headers=h,
            json={"data": bad["data"] | {"phone": "0300 5550104", "email": "nida@example.com"}},
        ).json()
        assert fixed["errors"] == {}
        done = client.post(f"{url}/commit", headers=h).json()
        assert (done["committed"], done["created"]) == (True, 4)
        assert len(client.get("/v1/ledger/accounts", headers=h).json()) == 4
        assert client.post(f"{url}/commit", headers=h).status_code == 409  # once
        assert (
            client.patch(f"{url}/rows/{bad['id']}", headers=h, json={"data": {}}).status_code == 409
        )

        # The imported customer can sign in with the username from the file.
        start = client.post(
            "/v1/customer/login/start",
            json={"phone": "0301 5550101", "username": "ali.haider", "bank_id": owner.org_id},
        ).json()
        assert start["bank"]["id"] == owner.org_id


def test_transactions_need_their_customer_and_never_double(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        owner = bank(client)
        h = owner.headers()
        orphan = client.post(
            "/v1/imports/manual",
            headers=h,
            json={
                "record_type": "transactions",
                "rows": [
                    {
                        "reference": "X1",
                        "customer_ref": "CUST-9999",
                        "occurred_at": "2026-10-20 10:00",
                        "description": "Shop",
                        "amount": "100",
                        "direction": "debit",
                    }
                ],
            },
        ).json()
        result = client.post(f"/v1/imports/{orphan['id']}/commit", headers=h).json()
        assert (result["committed"], result["blocked"]) == (False, 1)
        row = client.get(f"/v1/imports/{orphan['id']}", headers=h).json()["rows"][0]
        assert "import or add the customer first" in row["errors"]["customer_ref"]

        client.post("/v1/ledger/demo-data", headers=h)  # CUST-1001..1006 exist now
        batch = client.post(
            "/v1/imports/sample", headers=h, json={"record_type": "transactions"}
        ).json()
        [bad] = [r for r in batch["rows"] if r["errors"]]
        assert bad["errors"] == {"amount": "not a number"}
        url = f"/v1/imports/{batch['id']}"
        assert client.delete(f"{url}/rows/{bad['id']}", headers=h).status_code == 204
        assert client.post(f"{url}/commit", headers=h).json()["created"] == 4

        again = client.post(
            "/v1/imports/sample", headers=h, json={"record_type": "transactions"}
        ).json()
        client.delete(
            f"/v1/imports/{again['id']}/rows/{next(r['id'] for r in again['rows'] if r['errors'])}",
            headers=h,
        )
        twice = client.post(f"/v1/imports/{again['id']}/commit", headers=h).json()
        assert (twice["created"], twice["skipped_duplicates"]) == (0, 4)  # a re-imported statement
        txns = client.get("/v1/ledger/accounts/CUST-1002/transactions", headers=h).json()
        assert next(t for t in txns if t["reference"] == "TX-77003")["kind"] == "debit"


def test_only_admins_import_and_companies_stay_apart(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        owner = bank(client)
        batch = client.post(
            "/v1/imports/manual", headers=owner.headers(), json={"record_type": "customers"}
        ).json()
        member = signup(client)
        client.post("/v1/members", headers=owner.headers(), json={"email": member.email})
        as_member = member.headers(org_id=owner.org_id)
        assert client.get("/v1/imports", headers=as_member).status_code == 403
        outsider = bank(client)
        assert (
            client.get(f"/v1/imports/{batch['id']}", headers=outsider.headers()).status_code == 404
        )
        assert (
            client.post(
                "/v1/imports/manual", headers=owner.headers(), json={"record_type": "trips"}
            ).status_code
            == 422
        )  # not a bank's record type


# --- Reports: the extraction agent, then review -------------------------------------------------


async def test_report_rows_are_extracted_then_reviewed(
    client_for: Any, settings: Settings, sessions: Sessions
) -> None:
    extracted = schema_for(CUSTOMERS).model_validate(
        {
            "rows": [
                {
                    "customer_ref": "C-10",
                    "customer_name": "Asad Ali",
                    "phone": "0300 1110001",
                    "username": None,
                    "email": None,
                    "currency": None,
                },
                {
                    "customer_ref": "C-11",
                    "customer_name": None,
                    "phone": None,
                    "username": None,
                    "email": None,
                    "currency": None,
                },
            ]
        }
    )
    llm = FakeLLM(extracted)
    with client_for(llm) as client:
        owner = bank(client)
        report = b"Customer register\nC-10  Asad Ali  0300 1110001\nC-11  (name missing)\n"
        batch = client.post(
            "/v1/imports/file",
            headers=owner.headers(),
            data={"record_type": "customers"},
            files={"file": ("register.txt", report, "text/plain")},
        ).json()
        assert batch["status"] == "extracting"

        assert await process_next_import(sessions, llm, settings) is True
        done = client.get(f"/v1/imports/{batch['id']}", headers=owner.headers()).json()
        assert (done["status"], done["row_count"], done["error_count"]) == ("review", 2, 1)
        assert "C-10  Asad Ali" in llm.calls[0]["user"]  # the report, delimited, as data
        assert "<<<" in llm.calls[0]["user"]


# --- Transport: trips, and the agents answering from them -------------------------------------


async def test_transport_company_imports_trips_and_answers_seat_questions(
    client_for: Any, settings: Settings, sessions: Sessions, runtime: KnowledgeRuntime
) -> None:
    llm = FakeLLM(
        triage_result(category="general_inquiry", urgency="normal"),
        query_plan("seats to Lahore"),
        reply("Yes: the 17:00 bus to Lahore has 3 seats left at PKR 6,500."),
    )
    with client_for(llm) as client:
        owner = bank(client, industry="transport")
        h = owner.headers()
        profile = client.get("/v1/orgs/current/profile", headers=h).json()
        assert profile["capabilities"] == {
            "money_tools": False,
            "trips": True,
            "record_types": ["customers", "trips"],
        }
        names = [t["name"] for t in client.get("/v1/imports/templates", headers=h).json()]
        assert names == ["customers", "trips"]

        batch = client.post("/v1/imports/sample", headers=h, json={"record_type": "trips"}).json()
        [bad] = [r for r in batch["rows"] if r["errors"]]
        assert set(bad["errors"]) == {"departs_at", "seats_available"}
        client.delete(f"/v1/imports/{batch['id']}/rows/{bad['id']}", headers=h)
        assert client.post(f"/v1/imports/{batch['id']}/commit", headers=h).json()["created"] == 12

        client.post("/v1/ledger/demo-data", headers=h)  # passengers, no bank transactions
        assert client.get("/v1/ledger/accounts/CUST-1004/transactions", headers=h).json() == []
        tomorrow = datetime.now(UTC).astimezone(ZoneInfo("Asia/Karachi")) + timedelta(days=1)
        evening = f"KHI-LHE-{tomorrow:%m%d}-17"
        llm.script.append(
            proposal(action="answer_from_records").model_copy(
                update={"record_references": [evening]}
            )
        )
        customer = open_chat(client, owner, "CUST-1004")
        say(client, customer, "Is there a seat on the Karachi to Lahore bus tomorrow evening?")
        await run(sessions, settings, runtime, llm)

        resolve_call = next(c for c in llm.calls if "trips" in c["user"] and "R1" in c["user"])
        assert "KHI-LHE" not in resolve_call["user"].split('"trips"')[0]  # aliases, not refs
        [record] = chat(client, customer)["messages"][-1]["card"]["records"]
        assert (record["kind"], record["trip_ref"], record["origin"]) == (
            "trip",
            evening,
            "Karachi",
        )
