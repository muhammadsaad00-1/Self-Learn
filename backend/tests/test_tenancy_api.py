"""Org context, roles and membership management through the HTTP API, including
attempts to cross from one tenant into another."""

import uuid
from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.helpers import Account, bearer, signup


def add(client: TestClient, admin: Account, email: str, role: str = "member") -> int:
    resp = client.post("/v1/members", headers=admin.headers(), json={"email": email, "role": role})
    return resp.status_code


def set_role(client: TestClient, admin: Account, user_id: str, role: str) -> int:
    resp = client.patch(f"/v1/members/{user_id}", headers=admin.headers(), json={"role": role})
    return resp.status_code


def roles(client: TestClient, account: Account) -> dict[str, str]:
    members = client.get("/v1/members", headers=account.headers()).json()
    return {m["email"]: m["role"] for m in members}


# --- Org context ---------------------------------------------------------------------


def test_org_header_is_required(client: TestClient) -> None:
    owner = signup(client)
    assert client.get("/v1/members", headers=bearer(owner.token)).status_code == 422


def test_owner_sees_themselves_as_owner(client: TestClient) -> None:
    owner = signup(client)
    assert roles(client, owner) == {owner.email: "owner"}


def test_other_org_and_unknown_org_look_identical(client: TestClient) -> None:
    alice, bob = signup(client), signup(client)
    foreign = client.get("/v1/members", headers=alice.headers(org_id=bob.org_id))
    unknown = client.get("/v1/members", headers=alice.headers(org_id=str(uuid.uuid4())))
    assert foreign.status_code == unknown.status_code == 403
    assert foreign.json() == unknown.json()  # can't probe which org ids exist


def test_suspended_org_is_locked(
    client: TestClient, run_sql: Callable[[str, dict[str, object]], None]
) -> None:
    owner = signup(client)
    run_sql("UPDATE organizations SET status = 'suspended' WHERE id = :o", {"o": owner.org_id})
    assert client.get("/v1/members", headers=owner.headers()).status_code == 403


# --- Roles ---------------------------------------------------------------------------


def test_added_member_can_read_but_not_manage(client: TestClient) -> None:
    owner, joiner = signup(client), signup(client)
    assert add(client, owner, joiner.email) == 201
    as_member = joiner.headers(org_id=owner.org_id)
    assert client.get("/v1/members", headers=as_member).status_code == 200
    resp = client.post("/v1/members", headers=as_member, json={"email": "x@test.example"})
    assert resp.status_code == 403


def test_admin_manages_members_but_not_owners(client: TestClient) -> None:
    owner, admin, other = signup(client), signup(client), signup(client)
    add(client, owner, admin.email, "admin")
    as_admin = Account(admin.email, admin.token, admin.user_id, owner.org_id)
    assert add(client, as_admin, other.email, "admin") == 201  # admins can add admins
    assert set_role(client, as_admin, other.user_id, "owner") == 403  # but not grant owner
    assert set_role(client, as_admin, owner.user_id, "member") == 403  # or demote the owner


def test_add_unknown_or_existing_member(client: TestClient) -> None:
    owner = signup(client)
    assert add(client, owner, "nobody@test.example") == 404
    assert add(client, owner, owner.email) == 409


# --- Ownership invariants ------------------------------------------------------------


def test_last_owner_cannot_leave_or_be_demoted(client: TestClient) -> None:
    owner = signup(client)
    assert set_role(client, owner, owner.user_id, "admin") == 409
    resp = client.delete(f"/v1/members/{owner.user_id}", headers=owner.headers())
    assert resp.status_code == 409


def test_ownership_can_be_handed_over(client: TestClient) -> None:
    owner, successor = signup(client), signup(client)
    add(client, owner, successor.email, "owner")
    assert set_role(client, owner, owner.user_id, "member") == 200  # another owner exists now
    assert roles(client, owner) == {owner.email: "member", successor.email: "owner"}


# --- Access changes apply immediately ------------------------------------------------


def test_removed_member_loses_access_on_next_request(client: TestClient) -> None:
    owner, joiner = signup(client), signup(client)
    add(client, owner, joiner.email)
    as_member = joiner.headers(org_id=owner.org_id)
    assert client.get("/v1/members", headers=as_member).status_code == 200
    assert (
        client.delete(f"/v1/members/{joiner.user_id}", headers=owner.headers()).status_code == 204
    )
    assert client.get("/v1/members", headers=as_member).status_code == 403  # same token


# --- Cross-tenant attempts -----------------------------------------------------------


def test_admin_cannot_touch_members_of_another_org(client: TestClient) -> None:
    """Alice owns org A. Bob owns org B. Alice acts in her own org but targets Bob's id."""
    alice, bob = signup(client), signup(client)
    assert set_role(client, alice, bob.user_id, "member") == 404
    assert client.delete(f"/v1/members/{bob.user_id}", headers=alice.headers()).status_code == 404
    assert roles(client, bob) == {bob.email: "owner"}  # untouched
