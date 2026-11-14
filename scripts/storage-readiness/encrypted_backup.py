"""Authenticated archive for bounded disposable recovery rehearsals."""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HEADER = b"PRUFAI-SYNTHETIC-BACKUP/1\n"
NONCE_BYTES = 12
KEY_BYTES = 32
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_OBJECTS = 64
MAX_ENVELOPE_BYTES = len(HEADER) + NONCE_BYTES + MAX_ARCHIVE_BYTES + 16

Object = tuple[bytes, str, dict[str, str]]


class BackupRejected(ValueError):
    """The archive is unsuitable for restore."""


@dataclass(frozen=True)
class VerifiedBackup:
    dump: bytes
    objects: dict[str, Object]
    manifest_sha256: str


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seal(dump: bytes, objects: dict[str, Object], head: str, key: bytes) -> bytes:
    if (
        len(key) != KEY_BYTES
        or not dump.startswith(b"PGDMP")
        or len(objects) > MAX_OBJECTS
    ):
        raise BackupRejected("Invalid synthetic backup input")
    if (
        len(dump) + sum(len(payload) for payload, _, _ in objects.values())
        > MAX_ARCHIVE_BYTES
    ):
        raise BackupRejected("Synthetic backup exceeds its size limit")

    inventory = {}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("database.dump", dump)
        for index, (object_key, (payload, mime, metadata)) in enumerate(
            sorted(objects.items())
        ):
            name = f"object-{index}"
            archive.writestr(name, payload)
            inventory[object_key] = {
                "name": name,
                "sha256": checksum(payload),
                "bytes": len(payload),
                "type": mime,
                "metadata": metadata,
            }
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "alembic_head": head,
                    "database_sha256": checksum(dump),
                    "objects": inventory,
                },
                sort_keys=True,
            ),
        )

    if buffer.tell() > MAX_ARCHIVE_BYTES:
        raise BackupRejected("Synthetic backup exceeds its size limit")
    nonce = os.urandom(NONCE_BYTES)
    return HEADER + nonce + AESGCM(key).encrypt(nonce, buffer.getvalue(), HEADER)


def _read_members(cleartext: bytes, expected_head: str) -> VerifiedBackup:
    with zipfile.ZipFile(io.BytesIO(cleartext), "r") as archive:
        members = archive.infolist()
        names = {member.filename for member in members}
        if (
            len(names) != len(members)
            or len(members) > MAX_OBJECTS + 2
            or sum(member.file_size for member in members) > MAX_ARCHIVE_BYTES
            or any(member.compress_type != zipfile.ZIP_STORED for member in members)
        ):
            raise BackupRejected("Invalid synthetic backup inventory")

        manifest_bytes = archive.read("manifest.json")
        manifest = json.loads(manifest_bytes)
        if manifest["alembic_head"] != expected_head:
            raise BackupRejected("Synthetic backup migration head mismatch")
        dump = archive.read("database.dump")
        if (
            not dump.startswith(b"PGDMP")
            or checksum(dump) != manifest["database_sha256"]
        ):
            raise BackupRejected("Synthetic backup database checksum mismatch")

        restored: dict[str, Object] = {}
        expected = {"database.dump", "manifest.json"}
        for index, (object_key, record) in enumerate(
            sorted(manifest["objects"].items())
        ):
            name = f"object-{index}"
            if record["name"] != name:
                raise BackupRejected("Invalid synthetic backup inventory")
            payload = archive.read(name)
            if len(payload) != record["bytes"] or checksum(payload) != record["sha256"]:
                raise BackupRejected("Synthetic backup object checksum mismatch")
            mime, metadata = record["type"], record["metadata"]
            if (
                not isinstance(mime, str)
                or not isinstance(metadata, dict)
                or any(not isinstance(value, str) for value in metadata.values())
            ):
                raise BackupRejected("Invalid synthetic backup object metadata")
            restored[object_key] = payload, mime, metadata
            expected.add(name)
        if names != expected:
            raise BackupRejected("Invalid synthetic backup inventory")

    return VerifiedBackup(dump, restored, checksum(manifest_bytes))


def verify(envelope: bytes, key: bytes, expected_head: str) -> VerifiedBackup:
    """Authenticate the entire archive before parsing or returning any payload."""
    if len(key) != KEY_BYTES:
        raise BackupRejected("Synthetic backup authentication failed")
    split = len(HEADER) + NONCE_BYTES
    if (
        not envelope.startswith(HEADER)
        or not split + 16 < len(envelope) <= MAX_ENVELOPE_BYTES
    ):
        raise BackupRejected("Invalid synthetic backup envelope")
    try:
        cleartext = AESGCM(key).decrypt(
            envelope[len(HEADER) : split], envelope[split:], HEADER
        )
    except (InvalidTag, ValueError):
        raise BackupRejected("Synthetic backup authentication failed") from None
    try:
        return _read_members(cleartext, expected_head)
    except (
        zipfile.BadZipFile,
        KeyError,
        TypeError,
        AttributeError,
        UnicodeError,
        json.JSONDecodeError,
    ):
        raise BackupRejected("Invalid synthetic backup inventory") from None
