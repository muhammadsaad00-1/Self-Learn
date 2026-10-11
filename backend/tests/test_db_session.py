"""The per-request transaction (db.SessionDep) behaves as a unit of work."""

from fastapi.testclient import TestClient

from agentflow.config import Settings
from agentflow.db import SessionDep
from agentflow.identity.models import User
from agentflow.main import create_app


def test_failed_commit_is_not_reported_as_success(settings: Settings) -> None:
    """Regression: FastAPI's default runs dependency cleanup (our COMMIT) after the response
    is sent, so a failing commit still returned 201. SessionDep uses scope="function"."""
    app = create_app(settings)

    @app.post("/_test/duplicate-users", status_code=201)
    async def duplicate_users(session: SessionDep) -> None:
        # No flush: the INSERTs, and the unique-email violation, only happen at COMMIT.
        session.add(User(email="same@test.example", full_name="A", password_hash=None))
        session.add(User(email="same@test.example", full_name="B", password_hash=None))

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post("/_test/duplicate-users").status_code == 500
