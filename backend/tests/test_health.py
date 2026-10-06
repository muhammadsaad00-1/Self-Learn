from fastapi.testclient import TestClient

from agentflow.config import Settings
from agentflow.main import create_app


def test_health_ok() -> None:
    client = TestClient(create_app(Settings(env="test")))
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_docs_disabled_in_prod() -> None:
    client = TestClient(create_app(Settings(env="prod")))
    assert client.get("/docs").status_code == 404
