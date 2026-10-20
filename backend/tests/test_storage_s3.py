"""S3ObjectStore against a real S3 API (MinIO from docker compose; a service in CI).

Skipped when nothing listens on the configured endpoint, so a laptop without the full
stack can still run the rest of the suite.
"""

import socket
import uuid
from urllib.parse import urlparse

import pytest
from botocore.exceptions import ClientError

from agentflow.config import Settings
from agentflow.knowledge.storage import S3ObjectStore, object_key


def _s3_reachable() -> bool:
    url = urlparse(Settings().s3_endpoint_url or "")
    try:
        with socket.create_connection((url.hostname or "", url.port or 443), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _s3_reachable(), reason="no S3/MinIO endpoint running")


async def test_put_get_delete_round_trip() -> None:
    store = S3ObjectStore(Settings())
    await store.ensure_bucket()
    key = object_key(uuid.uuid4(), uuid.uuid4(), ".md")
    await store.put(key, b"# Refunds\nDuplicates are reversed.", "text/markdown")
    assert await store.get(key) == b"# Refunds\nDuplicates are reversed."
    await store.delete(key)
    with pytest.raises(ClientError):
        await store.get(key)


async def test_ensure_bucket_is_idempotent() -> None:
    store = S3ObjectStore(Settings())
    await store.ensure_bucket()
    await store.ensure_bucket()
