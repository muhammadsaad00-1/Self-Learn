"""Seat holds (identity-checked, exactly-once, never overbooked, expire back into inventory) and
tenant policy limits (versioned, audited, used by the action gate)."""

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from agentflow.db import set_tenant_context
from tests.fakes import FakeLLM
from tests.helpers import Account
from tests.test_actions import Sessions
from tests.test_imports import bank

CNIC = "42101-1234567-1"


def transport_company(client: TestClient) -> tuple[Account, dict[str, Any]]:
    owner = bank(client, industry="transport")
    h = owner.headers()
    batch = client.post(
        "/v1/imports/manual",
        headers=h,
        json={
            "record_type": "customers",
            "rows": [
                {
                    "customer_ref": "P-1",
                    "customer_name": "Sana Riaz",
                    "phone": "0300 1234567",
                    "cnic": CNIC,
                }
            ],
        },
    ).json()
    assert client.post(f"/v1/imports/{batch['id']}/commit", headers=h).json()["committed"]
    trips = client.post("/v1/imports/sample", headers=h, json={"record_type": "trips"}).json()
    for row in trips["rows"]:
        if row["errors"]:
            client.delete(f"/v1/imports/{trips['id']}/rows/{row['id']}", headers=h)
    client.post(f"/v1/imports/{trips['id']}/commit", headers=h)
    trip = max(client.get("/v1/bookings/trips", headers=h).json(), key=lambda t: t["departs_at"])
    return owner, trip


def book(client: TestClient, owner: Account, trip: dict[str, Any], **change: Any) -> Any:
    body = {
        "trip_id": trip["id"],
        "customer_ref": "P-1",
        "phone": "0300-1234567",
        "cnic": CNIC,
        "seats": 2,
        "idempotency_key": str(uuid.uuid4()),
    } | change
    return client.post("/v1/bookings", headers=owner.headers(), json=body)


def seats_left(client: TestClient, owner: Account, trip: dict[str, Any]) -> int:
    trips = client.get("/v1/bookings/trips", headers=owner.headers()).json()
    return int(next(t for t in trips if t["id"] == trip["id"])["seats_available"])


def test_hold_checks_identity_and_is_exactly_once(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        owner, trip = transport_company(client)
        start = trip["seats_available"]

        # Wrong CNIC, same 403 as a wrong phone: the caller can't tell which field failed.
        assert book(client, owner, trip, cnic="42101-7654321-1").status_code == 403
        assert book(client, owner, trip, phone="0300 7654321").status_code == 403

        key = str(uuid.uuid4())
        first = book(client, owner, trip, idempotency_key=key)
        assert first.status_code == 201, first.text
        assert first.json()["status"] == "held"
        assert first.json()["expires_at"]
        again = book(client, owner, trip, idempotency_key=key)  # client retry
        assert again.json()["id"] == first.json()["id"]
        assert seats_left(client, owner, trip) == start - 2  # held once, not twice
        assert book(client, owner, trip, idempotency_key=key, seats=3).status_code == 409

        # Fill the bus: the hold that no longer fits is refused and inventory never goes negative.
        while (resp := book(client, owner, trip, seats=6)).status_code == 201:
            pass
        assert resp.status_code == 409
        assert "Not enough seats" in resp.json()["detail"]
        assert 0 <= seats_left(client, owner, trip) < 6

        booking_id = first.json()["id"]
        url = f"/v1/bookings/{booking_id}"
        assert client.post(f"{url}/cancel", headers=owner.headers()).json() == {
            "status": "cancelled"
        }
        assert client.post(f"{url}/cancel", headers=owner.headers()).status_code == 409
        assert client.post(f"{url}/confirm", headers=owner.headers()).status_code == 409


async def test_expired_holds_return_their_seats(client_for: Any, sessions: Sessions) -> None:
    with client_for(FakeLLM()) as client:
        owner, trip = transport_company(client)
        start = trip["seats_available"]
        held = book(client, owner, trip).json()
        assert seats_left(client, owner, trip) == start - 2

        org_id = uuid.UUID(owner.org_id)
        async with sessions() as session, session.begin():
            await set_tenant_context(session, org_id=org_id)
            await session.execute(
                text(
                    "UPDATE seat_bookings SET expires_at = now() - interval '1 minute' "
                    "WHERE id = :id"
                ),
                {"id": held["id"]},
            )
        async with sessions() as session, session.begin():
            expired = (await session.execute(text("SELECT * FROM expire_seat_holds()"))).all()
        assert [str(e.booking_id) for e in expired if e.org_id == org_id] == [held["id"]]

        assert seats_left(client, owner, trip) == start
        url = f"/v1/bookings/{held['id']}/confirm"
        assert client.post(url, headers=owner.headers()).status_code == 409


def test_policy_limits_are_versioned_validated_and_audited(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        owner = bank(client)
        h = owner.headers()
        default = client.get("/v1/policy/current", headers=h).json()
        assert default["version"] == 0  # code defaults until the tenant saves its own

        limits = {"auto_approve_limit": "500", "max_refund": "20000", "max_reversals_per_day": 2}
        assert client.put("/v1/policy/current", headers=h, json=limits).json()["version"] == 1
        assert client.put("/v1/policy/current", headers=h, json=limits).json()["version"] == 2
        saved = client.get("/v1/policy/current", headers=h).json()
        assert (saved["auto_approve_limit"], saved["max_refund"]) == ("500.00", "20000.00")

        inverted = limits | {"auto_approve_limit": "30000"}  # auto above the hard maximum
        assert client.put("/v1/policy/current", headers=h, json=inverted).status_code == 422

        events = client.get(
            "/v1/audit/events", headers=h, params={"action": "policy.updated"}
        ).json()["events"]
        assert [e["details"]["version"] for e in events] == [2, 1]
