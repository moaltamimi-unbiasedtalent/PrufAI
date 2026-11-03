from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import (
    CertificateStatus,
    CertificationAuthorizationStatus,
    ReleaseStatus,
    StatementStatus,
)
from app.models.identity import enum_type


class ResultRelease(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "result_releases"
    __table_args__ = (
        UniqueConstraint("result_version_id", "operation_id", name="uq_result_release_operation"),
        UniqueConstraint("result_version_id", name="uq_result_release_version"),
        UniqueConstraint("supersedes_release_id", name="uq_release_successor"),
        Index("ix_result_releases_candidate", "candidate_user_id", "organisation_id"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id"), nullable=False
    )
    assessment_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_results.id"), nullable=False
    )
    result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("result_versions.id"), nullable=False
    )
    scoring_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scoring_runs.id"), nullable=False)
    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id"), nullable=False
    )
    released_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[ReleaseStatus] = mapped_column(enum_type(ReleaseStatus), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_release_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("result_releases.id")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )


class StatementOfResults(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "statements_of_results"
    __table_args__ = (
        UniqueConstraint("release_id", "operation_id", name="uq_statement_release_operation"),
        UniqueConstraint("release_id", name="uq_statement_release"),
    )

    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("result_releases.id"), nullable=False)
    result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("result_versions.id"), nullable=False
    )
    scoring_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scoring_runs.id"), nullable=False)
    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id"), nullable=False
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id"), nullable=False
    )
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    statement_version: Mapped[int] = mapped_column(Integer, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[StatementStatus] = mapped_column(enum_type(StatementStatus), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class CertificationAuthorization(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "certification_authorizations"
    __table_args__ = (
        Index("ix_certification_authorizations_scope", "organisation_id", "status"),
        UniqueConstraint("organisation_id", "operation_id", name="uq_authorization_operation"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    assessment_scope: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_scope: Mapped[str] = mapped_column(String(120), nullable=False)
    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id"), nullable=False
    )
    operation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[CertificationAuthorizationStatus] = mapped_column(
        enum_type(CertificationAuthorizationStatus), nullable=False
    )
    evidence_reference: Mapped[str] = mapped_column(String(240), nullable=False)
    approved_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CertificateRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "certificate_records"
    __table_args__ = (
        UniqueConstraint("release_id", "operation_id", name="uq_certificate_release_operation"),
        UniqueConstraint("release_id", name="uq_certificate_release"),
    )

    release_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("result_releases.id"), nullable=False)
    result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("result_versions.id"), nullable=False
    )
    authorization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("certification_authorizations.id"), nullable=False
    )
    authorization_version: Mapped[int] = mapped_column(Integer, nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    serial: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CertificateStatus] = mapped_column(enum_type(CertificateStatus), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(120), nullable=False)


def _immutable_provenance(_mapper: Any, _connection: Any, target: Any) -> None:
    changed = {
        attribute.key for attribute in inspect(target).attrs if attribute.history.has_changes()
    }
    permitted = (
        {"status"} if isinstance(target, (ResultRelease, CertificationAuthorization)) else set()
    )
    if changed - permitted:
        raise ValueError("released provenance and artifacts are immutable; create a successor")
    if "status" in changed:
        history = inspect(target).attrs.status.history
        if history.deleted and history.deleted[0].value not in {"ACTIVE", "RELEASED"}:
            raise ValueError("a terminal historical record cannot be reactivated")
        if target.status.value not in {"REVOKED", "SUPERSEDED", "EXPIRED"}:
            raise ValueError("only an audited terminal lifecycle transition is permitted")


def _retain_history(_mapper: Any, _connection: Any, _target: Any) -> None:
    raise ValueError("release, authorization and artifact history cannot be deleted")


for _model in (ResultRelease, StatementOfResults, CertificationAuthorization, CertificateRecord):
    event.listen(_model, "before_update", _immutable_provenance)
    event.listen(_model, "before_delete", _retain_history)
