"""Object storage for original uploaded files (S3 API: MinIO locally, AWS S3 in the cloud).

Files never go into Postgres: they'd bloat backups and the buffer cache. The database keeps
metadata plus the object key. Keys start with the org id, so an IAM policy can restrict a
principal to one tenant's prefix later (spec 02 §5).
"""

import asyncio
import uuid
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from agentflow.config import Settings


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...


def object_key(org_id: uuid.UUID, document_id: uuid.UUID, extension: str) -> str:
    """Tenant-prefixed key. The user's filename is not part of it (no path tricks, no PII)."""
    return f"orgs/{org_id}/documents/{document_id}{extension}"


class S3ObjectStore:
    """boto3 is synchronous, so each call runs in a thread instead of blocking the loop."""

    def __init__(self, settings: Settings) -> None:
        secret = settings.s3_secret_access_key
        self._bucket = settings.s3_bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=secret.get_secret_value() if secret else None,
            region_name=settings.s3_region,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    async def ensure_bucket(self) -> None:
        """Local convenience: create the bucket if missing. On AWS, Terraform owns it."""

        def run() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError:
                self._client.create_bucket(Bucket=self._bucket)

        await asyncio.to_thread(run)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        def run() -> bytes:
            body: bytes = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
            return body

        return await asyncio.to_thread(run)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)
