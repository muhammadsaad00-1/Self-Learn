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


def test_ready_when_dependencies_up(database_url: str) -> None:
    # `with` runs the app's lifespan, which opens the DB and Redis pools.
    with TestClient(create_app(Settings(env="test", database_url=database_url))) as client:
        resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"] == {"database": "ok", "redis": "ok"}


def test_not_ready_when_database_down() -> None:
    down = "postgresql+asyncpg://nobody:x@127.0.0.1:1/none"  # nothing listens on port 1
    with TestClient(create_app(Settings(env="test", database_url=down))) as client:
        resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"] == "unavailable"
