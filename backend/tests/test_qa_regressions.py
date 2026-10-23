"""Bugs found in the QA pass, each pinned by a test."""

from typing import Any

from tests.fakes import FakeLLM
from tests.helpers import PASSWORD, signup
from tests.test_chat import conversations, open_chat


def test_whitespace_only_text_is_rejected_not_a_server_error(client_for: Any) -> None:
    """A message of spaces passed min_length=1, was stripped to "" and crashed (500)."""
    with client_for(FakeLLM()) as client:
        owner = signup(client)
        client.post("/v1/ledger/demo-data", headers=owner.headers())
        customer = open_chat(client, owner, "CUST-1001")
        help_id = conversations(client, customer)[0]["id"]
        url = f"/v1/customer/conversations/{help_id}/messages"
        assert client.post(url, headers=customer, json={"text": "   \n\t "}).status_code == 422
        assert (
            client.post("/v1/customer/issues", headers=customer, json={"text": "  "}).status_code
            == 422
        )
        ok = client.post(url, headers=customer, json={"text": "  hello  "})
        assert (ok.status_code, ok.json()["body"]) == (201, "hello")  # trimmed, not rejected


def test_blank_names_are_rejected_at_signup(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        for field in ("full_name", "org_name"):
            body = {
                "email": f"blank-{field}@example.com",
                "password": PASSWORD,
                "full_name": "Sana",
                "org_name": "Bank",
            } | {field: "   "}
            assert client.post("/v1/auth/signup", json=body).status_code == 422


def test_phone_must_be_a_number(client_for: Any) -> None:
    with client_for(FakeLLM()) as client:
        start = "/v1/customer/login/start"
        assert (
            client.post(start, json={"phone": "abcdefghijk", "username": "x.y"}).status_code == 422
        )
        assert (
            client.post(start, json={"phone": "0300 1234567", "username": "x.y"}).status_code == 200
        )
