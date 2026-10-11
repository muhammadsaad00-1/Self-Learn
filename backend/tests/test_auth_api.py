"""Signup, login and /me through the real HTTP API."""

import uuid
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from agentflow.config import Settings
from agentflow.main import create_app

PASSWORD = "correct horse battery staple"


def new_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@test.example"


def signup(client: TestClient, email: str, org_name: str = "Daewoo Express") -> dict[str, Any]:
    resp = client.post(
        "/v1/auth/signup",
        json={"email": email, "password": PASSWORD, "full_name": "Ayesha", "org_name": org_name},
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def login(client: TestClient, email: str, password: str = PASSWORD) -> int:
    return client.post("/v1/auth/login", data={"username": email, "password": password}).status_code


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_signup_logs_in_and_makes_user_owner(client: TestClient) -> None:
    body = signup(client, new_email())
    me = client.get("/v1/me", headers=bearer(body["access_token"])).json()
    assert me["user"]["id"] == body["user"]["id"]
    assert me["memberships"] == [
        {
            "org_id": body["org_id"],
            "org_name": "Daewoo Express",
            "org_slug": me["memberships"][0]["org_slug"],
            "role": "owner",
        }
    ]
    assert me["memberships"][0]["org_slug"].startswith("daewoo-express")
    assert "password_hash" not in me["user"]


def test_email_is_case_insensitive(client: TestClient) -> None:
    email = new_email()
    signup(client, email.upper())
    assert login(client, email) == 200
    dup = client.post(
        "/v1/auth/signup",
        json={"email": email, "password": PASSWORD, "full_name": "X", "org_name": "Y"},
    )
    assert dup.status_code == 409


def test_short_password_rejected(client: TestClient) -> None:
    resp = client.post(
        "/v1/auth/signup",
        json={"email": new_email(), "password": "short", "full_name": "X", "org_name": "Y"},
    )
    assert resp.status_code == 422


def test_wrong_password_and_unknown_email_look_identical(client: TestClient) -> None:
    email = new_email()
    signup(client, email)
    wrong = client.post("/v1/auth/login", data={"username": email, "password": "nope-nope-nope"})
    unknown = client.post(
        "/v1/auth/login", data={"username": new_email(), "password": "nope-nope-nope"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_login_token_works_for_me(client: TestClient) -> None:
    email = new_email()
    signup(client, email)
    token = client.post("/v1/auth/login", data={"username": email, "password": PASSWORD}).json()
    assert client.get("/v1/me", headers=bearer(token["access_token"])).status_code == 200


def test_me_rejects_missing_and_bad_tokens(client: TestClient) -> None:
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers=bearer("not-a-jwt")).status_code == 401
    token = signup(client, new_email())["access_token"]
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert client.get("/v1/me", headers=bearer(tampered)).status_code == 401


def test_expired_token_rejected(settings: Settings) -> None:
    expired = settings.model_copy(update={"access_token_ttl_minutes": -1})
    with TestClient(create_app(expired)) as client:
        token = signup(client, new_email())["access_token"]
        assert client.get("/v1/me", headers=bearer(token)).status_code == 401


def test_disabled_user_locked_out_immediately(
    client: TestClient, run_sql: Callable[[str, dict[str, object]], None]
) -> None:
    email = new_email()
    token = signup(client, email)["access_token"]
    run_sql("UPDATE users SET status = 'disabled' WHERE email = :e", {"e": email})
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401  # old token dead
    assert login(client, email) == 401


def test_login_rate_limited(settings: Settings) -> None:
    strict = settings.model_copy(update={"login_max_attempts": 3})
    with TestClient(create_app(strict)) as client:
        email = new_email()
        signup(client, email)
        assert [login(client, email, "wrong-password!") for _ in range(3)] == [401, 401, 401]
        resp = client.post("/v1/auth/login", data={"username": email, "password": PASSWORD})
        assert resp.status_code == 429  # even the right password, until the window ends
        assert int(resp.headers["Retry-After"]) > 0
