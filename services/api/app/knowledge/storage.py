from __future__ import annotations

import mimetypes
import re
import uuid
from dataclasses import dataclass
from typing import Optional, Protocol

ALLOWED_MIME_TYPES = {"application/pdf", "text/plain", "text/markdown"}
MAX_SOURCE_FILE_SIZE = 25 * 1024 * 1024


class StorageError(ValueError):
    pass


class ObjectStorage(Protocol):
    def put(self, key: str, content: bytes, mime_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...


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


def safe_filename(filename: str) -> str:
    candidate = filename.replace("\\", "/").split("/")[-1]
    candidate = re.sub(r"[^A-Za-z0-9._-]", "_", candidate).strip(".")
    if not candidate:
        raise StorageError("filename is required")
    return candidate[:255]


def build_storage_key(source_id: uuid.UUID, version_id: uuid.UUID, filename: str) -> str:
    return f"standards/{source_id}/{version_id}/{uuid.uuid4()}-{safe_filename(filename)}"


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
