""" "Add company": one login, several companies, nothing shared between them."""

import uuid
from typing import Any

from fastapi.testclient import TestClient

from agentflow.config import Settings
from agentflow.main import create_app
from tests.helpers import PASSWORD, Account, bearer, login, signup


def add_company(client: TestClient, account: Account, name: str, password: str = PASSWORD) -> Any:
    return client.post(
        "/v1/orgs", headers=bearer(account.token), json={"org_name": name, "password": password}
    )


def test_add_company_makes_user_owner_of_a_second_org(client: TestClient) -> None:
    sara = signup(client, org_name="EasyPay Wallet")
    resp = add_company(client, sara, "EasyPay Lending")
    assert resp.status_code == 201
    new = resp.json()
    assert (new["org_name"], new["role"]) == ("EasyPay Lending", "owner")
    assert new["org_id"] != sara.org_id

    me = client.get("/v1/me", headers=bearer(sara.token)).json()
    assert sorted(m["org_name"] for m in me["memberships"]) == ["EasyPay Lending", "EasyPay Wallet"]
    assert login(client, sara.email) == 200  # same email and password for both


def test_new_company_starts_empty_and_separate(client: TestClient) -> None:
    sara, hina = signup(client), signup(client)
    client.post("/v1/members", headers=sara.headers(), json={"email": hina.email})
    client.post(
        "/v1/workflows",
        headers={**sara.headers(), "Idempotency-Key": uuid.uuid4().hex},
        json={"message": "Charged twice"},
    )
    new_org = add_company(client, sara, "Second Co").json()["org_id"]
    in_new = sara.headers(org_id=new_org)

    members = client.get("/v1/members", headers=in_new).json()
    assert [m["email"] for m in members] == [sara.email]  # Hina was not copied over
    assert client.get("/v1/workflows", headers=in_new).json() == []
    trail = client.get("/v1/audit/events", headers=in_new).json()["events"]
    assert [(e["action"], e["details"]) for e in trail] == [("org.created", {"via": "add_company"})]


def test_wrong_password_creates_nothing_and_keeps_session(client: TestClient) -> None:
    sara = signup(client)
    resp = add_company(client, sara, "Nope Co", password="not-my-password")
    assert resp.status_code == 403  # not 401: the dashboard must not log the user out
    me = client.get("/v1/me", headers=bearer(sara.token))
    assert me.status_code == 200
    assert len(me.json()["memberships"]) == 1


def test_requires_login(client: TestClient) -> None:
    resp = client.post("/v1/orgs", json={"org_name": "X", "password": PASSWORD})
    assert resp.status_code == 401


def test_password_guessing_shares_the_login_rate_limit(settings: Settings) -> None:
    strict = settings.model_copy(update={"login_max_attempts": 2})
    with TestClient(create_app(strict)) as client:
        sara = signup(client)
        assert add_company(client, sara, "A", password="guess-one").status_code == 403
        assert add_company(client, sara, "B", password="guess-two").status_code == 403
        assert add_company(client, sara, "C").status_code == 429  # even the right password
        assert login(client, sara.email) == 429  # and login is locked too
