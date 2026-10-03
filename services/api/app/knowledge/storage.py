from __future__ import annotations

import mimetypes
import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Protocol

ALLOWED_MIME_TYPES = {"application/pdf", "text/plain", "text/markdown"}
MAX_SOURCE_FILE_SIZE = 25 * 1024 * 1024


class StorageError(ValueError):
    pass


class ObjectStorage(Protocol):
    def put(self, key: str, content: bytes, mime_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def size(self, key: str) -> int: ...

    def read_range(self, key: str, start: int, end: int) -> bytes: ...


class S3CompatibleObjectStorage:
    """Private S3/MinIO adapter; callers provide a fake client in unit tests."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        secure: bool = True,
        client: Optional[Any] = None,
    ) -> None:
        if not endpoint or not bucket or not access_key or not secret_key:
            raise StorageError("S3-compatible object storage is not fully configured")
        self.bucket = bucket
        if client is not None:
            self.client = client
            return
        try:
            import boto3  # type: ignore[import-untyped]  # boto3 ships without py.typed metadata
        except ImportError as exc:
            raise StorageError("S3-compatible object storage client is unavailable") from exc
        scheme_endpoint = endpoint
        if not scheme_endpoint.startswith(("http://", "https://")):
            scheme_endpoint = f"{'https' if secure else 'http'}://{scheme_endpoint}"
        self.client = boto3.client(
            "s3",
            endpoint_url=scheme_endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def put(self, key: str, content: bytes, mime_type: str) -> None:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=mime_type,
                ACL="private",
            )
        except Exception as exc:
            raise StorageError("object storage is unavailable") from exc

    def get(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return bytes(response["Body"].read())
        except Exception as exc:
            raise StorageError("source file is unavailable") from exc

    def size(self, key: str) -> int:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
            return int(response["ContentLength"])
        except Exception as exc:
            raise StorageError("source file is unavailable") from exc

    def read_range(self, key: str, start: int, end: int) -> bytes:
        if start < 0 or end < start:
            raise StorageError("invalid byte range")
        try:
            response = self.client.get_object(
                Bucket=self.bucket,
                Key=key,
                Range=f"bytes={start}-{end}",
            )
            return bytes(response["Body"].read())
        except Exception as exc:
            raise StorageError("source file is unavailable") from exc

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


def build_object_storage(settings: Any) -> ObjectStorage:
    if settings.object_storage_backend == "memory":
        if settings.environment == "production":
            raise StorageError("production object storage cannot use in-memory storage")
        return InMemoryObjectStorage()
    if settings.object_storage_backend != "s3":
        raise StorageError("unsupported object storage backend")
    return S3CompatibleObjectStorage(
        endpoint=settings.object_storage_endpoint,
        bucket=settings.object_storage_bucket,
        access_key=settings.object_storage_access_key,
        secret_key=settings.object_storage_secret_key,
        region=settings.object_storage_region,
        secure=settings.object_storage_secure,
    )


@dataclass
class InMemoryObjectStorage:
    objects: dict[str, bytes]

    def __init__(self) -> None:
        self.objects = {}

    def put(self, key: str, content: bytes, mime_type: str) -> None:
        del mime_type
        self.objects[key] = content

    def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError as exc:
            raise StorageError("source file is unavailable") from exc

    def size(self, key: str) -> int:
        return len(self.get(key))

    def read_range(self, key: str, start: int, end: int) -> bytes:
        if start < 0 or end < start:
            raise StorageError("invalid byte range")
        return self.get(key)[start : end + 1]


def safe_filename(filename: str) -> str:
    candidate = filename.replace("\\", "/").split("/")[-1]
    candidate = re.sub(r"[^A-Za-z0-9._-]", "_", candidate).strip(".")
    if not candidate:
        raise StorageError("filename is required")
    return candidate[:255]


def build_storage_key(source_id: uuid.UUID, version_id: uuid.UUID, filename: str) -> str:
    del filename
    return f"standards/{source_id}/{version_id}/{uuid.uuid4()}"


def detect_mime_type(filename: str, declared_mime: Optional[str], content: bytes) -> str:
    if not content:
        raise StorageError("source file must not be empty")
    if len(content) > MAX_SOURCE_FILE_SIZE:
        raise StorageError("source file exceeds maximum size")
    extension_mime = mimetypes.guess_type(filename)[0]
    if content.startswith(b"%PDF-"):
        detected = "application/pdf"
    elif extension_mime in {"text/plain", "text/markdown"} or declared_mime in {
        "text/plain",
        "text/markdown",
    }:
        detected = declared_mime or extension_mime or "text/plain"
    else:
        raise StorageError("unsupported or unrecognised source file type")
    if declared_mime and declared_mime not in ALLOWED_MIME_TYPES:
        raise StorageError("unsupported MIME type")
    if declared_mime == "application/pdf" and detected != "application/pdf":
        raise StorageError("declared PDF does not have a PDF signature")
    return detected
