"""Small helpers shared by API tests."""

import uuid
from dataclasses import dataclass

from fastapi.testclient import TestClient

PASSWORD = "correct horse battery staple"


def new_email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@test.example"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Account:
    """A signed-up user and the organization they own."""

    email: str
    token: str
    user_id: str
    org_id: str

    def headers(self, org_id: str | None = None) -> dict[str, str]:
        """Auth header plus X-Org-Id (their own org unless another is given)."""
        return {**bearer(self.token), "X-Org-Id": org_id or self.org_id}


def signup(client: TestClient, email: str | None = None, org_name: str = "Acme Pay") -> Account:
    email = email or new_email()
    resp = client.post(
        "/v1/auth/signup",
        json={"email": email, "password": PASSWORD, "full_name": "Test User", "org_name": org_name},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return Account(email.lower(), body["access_token"], body["user"]["id"], body["org_id"])


def login(client: TestClient, email: str, password: str = PASSWORD) -> int:
    return client.post("/v1/auth/login", data={"username": email, "password": password}).status_code
