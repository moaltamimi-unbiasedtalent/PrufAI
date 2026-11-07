"""Recovery accepts only an independently provisioned, host-owned trust root."""

import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.evaluation.disposable import assert_disposable_database
from app.scoped_source_review.schemas import SHA256, CreateGrant, grant_scope_checksum
from app.writing_assessment.service import canonical_checksum

TRUST_DIRECTORY = "/etc/prufai"
TRUST_FILE = "initial-review-authority.json"
MAX_ARTIFACT_BYTES = 32768
MAX_WINDOW = timedelta(days=7)


class RecoveryError(Exception):
    """Only fixed, privacy-safe error codes cross the operation boundary."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Target(StrictModel):
    environment: Literal["S5_EDITORIAL", "DISPOSABLE_TEST"]
    database: str
    port: int = Field(ge=1, le=65535)
    cluster_id: str = Field(pattern=r"^[0-9]{1,20}$")
    database_oid: int = Field(gt=0)

    @model_validator(mode="after")
    def require_designation(self) -> "Target":
        if self.environment == "S5_EDITORIAL":
            if self.database != "prufai_s5_editorial" or self.port != 55448:
                raise ValueError("invalid editorial target")
        else:
            if not re.fullmatch(r"prufai_eval2_[a-z]+_[a-f0-9]{32}", self.database):
                raise ValueError("fresh disposable child database required")
            url = f"postgresql+psycopg://prufai@127.0.0.1:{self.port}/{self.database}"
            assert_disposable_database(url, self.database, report=False)
        return self


class TrustRoot(StrictModel):
    target: Target
    organisation_id: UUID
    approver_id: UUID
    approver_public_key: str = Field(pattern=SHA256)
    executor_id: UUID
    executor_public_key: str = Field(pattern=SHA256)
    executor_uid: int = Field(gt=0)
    revoked_nonces: set[UUID] = Field(default_factory=set, max_length=1000)

    @model_validator(mode="after")
    def require_independent_keys(self) -> "TrustRoot":
        if (
            self.approver_id == self.executor_id
            or self.approver_public_key == self.executor_public_key
        ):
            raise ValueError("independent signer identities and keys required")
        return self


class Manifest(StrictModel):
    version: Literal[1]
    action: Literal["ESTABLISH", "SUSPEND", "REVOKE"]
    target: Target
    organisation_id: UUID
    approver_id: UUID
    executor_id: UUID
    beneficiary_id: UUID
    beneficiary_identity_evidence: str = Field(pattern=SHA256)
    approval_evidence: str = Field(pattern=SHA256)
    revocation_responsibility: UUID
    reason: Literal["INITIAL_SCOPED_REVIEW_AUTHORITY", "END_SCOPED_REVIEW_AUTHORITY"]
    nonce: UUID
    starts_at: AwareDatetime
    expires_at: AwareDatetime
    grant: CreateGrant | None = None
    original_manifest_digest: str | None = Field(default=None, pattern=SHA256)

    @field_validator("starts_at", "expires_at", mode="before")
    @classmethod
    def offset_required(cls, value: object) -> object:
        if not isinstance(value, str) or not re.search(r"(?:Z|[+-][0-9]{2}:[0-9]{2})$", value):
            raise ValueError("explicit UTC offset required")
        return value

    @model_validator(mode="after")
    def validate_scope(self) -> "Manifest":
        self.starts_at = self.starts_at.astimezone(timezone.utc)
        self.expires_at = self.expires_at.astimezone(timezone.utc)
        if not timedelta(0) < self.expires_at - self.starts_at <= MAX_WINDOW:
            raise ValueError("invalid recovery validity window")
        if len({self.approver_id, self.executor_id, self.beneficiary_id}) != 3:
            raise ValueError("signers and beneficiary must be distinct")
        if self.revocation_responsibility != self.approver_id:
            raise ValueError("the independent approver must own revocation")
        if self.action == "ESTABLISH":
            grant = self.grant
            if (
                grant is None
                or self.original_manifest_digest is not None
                or self.reason != "INITIAL_SCOPED_REVIEW_AUTHORITY"
                or grant.reviewer_user_id
                in {self.approver_id, self.executor_id, self.beneficiary_id}
                or not self.starts_at <= grant.starts_at < grant.expires_at <= self.expires_at
                or grant.approved_scope_checksum
                != grant_scope_checksum(self.organisation_id, self.beneficiary_id, grant)
            ):
                raise ValueError("one independently approved exact review grant is required")
        elif (
            self.grant is not None
            or self.original_manifest_digest is None
            or self.reason != "END_SCOPED_REVIEW_AUTHORITY"
        ):
            raise ValueError("termination must reference the original manifest")
        return self


class Envelope(StrictModel):
    manifest: dict[str, Any]
    manifest_digest: str = Field(pattern=SHA256)
    approver_signature: str = Field(pattern=r"^[a-f0-9]{128}$")
    executor_signature: str = Field(pattern=r"^[a-f0-9]{128}$")


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError("DUPLICATE_ARTIFACT_KEY")
        result[key] = value
    return result


def parse_json(raw: bytes) -> Any:
    def reject_constant(_: str) -> Any:
        raise RecoveryError("INVALID_ARTIFACT_VALUE")

    return json.loads(raw, object_pairs_hook=_unique_keys, parse_constant=reject_constant)


def read_bounded(fd: int) -> bytes:
    with os.fdopen(fd, "rb") as file:
        result = file.read(MAX_ARTIFACT_BYTES + 1)
    if len(result) > MAX_ARTIFACT_BYTES:
        raise RecoveryError("ARTIFACT_TOO_LARGE")
    return result


def load_trust() -> TrustRoot:
    # The fixed directory has a trusted system parent. Open both without
    # following links and check the descriptors, including after replacements.
    directory = os.open(TRUST_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise RecoveryError("UNSAFE_TRUST_DIRECTORY")
        fd = os.open(TRUST_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            os.close(fd)
            raise RecoveryError("UNSAFE_TRUST_FILE")
        return TrustRoot.model_validate(parse_json(read_bounded(fd)))
    finally:
        os.close(directory)


def verify(envelope: Envelope, root: TrustRoot) -> Manifest:
    if canonical_checksum(envelope.manifest) != envelope.manifest_digest:
        raise RecoveryError("MANIFEST_DIGEST_MISMATCH")
    try:
        manifest = Manifest.model_validate(envelope.manifest)
        if (
            manifest.target != root.target
            or manifest.organisation_id != root.organisation_id
            or manifest.approver_id != root.approver_id
            or manifest.executor_id != root.executor_id
            or manifest.nonce in root.revoked_nonces
        ):
            raise RecoveryError("UNTRUSTED_OR_REVOKED_APPROVAL")
        message = b"PrufAI initial review authority v1\x00" + bytes.fromhex(
            envelope.manifest_digest
        )
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(root.approver_public_key)).verify(
            bytes.fromhex(envelope.approver_signature), b"APPROVER\x00" + message
        )
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(root.executor_public_key)).verify(
            bytes.fromhex(envelope.executor_signature), b"EXECUTOR\x00" + message
        )
        return manifest
    except (ValidationError, InvalidSignature, ValueError) as exc:
        raise RecoveryError("INVALID_MANIFEST_OR_SIGNATURE") from exc


def verify_url(url: URL, target: Target) -> None:
    if (
        url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or url.port != target.port
        or url.database != target.database
        or not url.username
        or url.query
    ):
        raise RecoveryError("UNSAFE_TARGET_URL")


def verify_database(session: Session, target: Target) -> None:
    row = session.execute(
        text("""
        SELECT pg_catalog.current_database(), pg_catalog.host(pg_catalog.inet_server_addr()),
               pg_catalog.inet_server_port(),
               (SELECT system_identifier::text FROM pg_catalog.pg_control_system()),
               (SELECT oid::int8 FROM pg_catalog.pg_database
                WHERE datname = pg_catalog.current_database()),
               pg_catalog.pg_is_in_recovery()
        """)
    ).one()
    if tuple(row) != (
        target.database,
        "127.0.0.1",
        target.port,
        target.cluster_id,
        target.database_oid,
        False,
    ):
        raise RecoveryError("DATABASE_IDENTITY_MISMATCH")


def require_active(manifest: Manifest, now: datetime) -> None:
    if not manifest.starts_at <= now < manifest.expires_at:
        raise RecoveryError("APPROVAL_OUTSIDE_VALIDITY_WINDOW")
