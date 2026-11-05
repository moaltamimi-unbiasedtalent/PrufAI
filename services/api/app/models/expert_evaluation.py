"""Reference governance attached to the existing evaluation dataset registry."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    event,
    inspect,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class EvaluationReference(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_references"
    __table_args__ = (
        UniqueConstraint("dataset_id", "checksum", name="uq_evaluation_reference_version"),
        CheckConstraint("status IN ('PENDING_APPROVAL','APPROVED')", name="ck_reference_state"),
        CheckConstraint(
            "approved_by IS NULL OR approved_by <> created_by", name="ck_reference_independent"
        ),
        CheckConstraint(
            "split IN ('TRAIN','CALIBRATION','VALIDATION','HELD_OUT')", name="ck_reference_split"
        ),
        CheckConstraint(
            "evidence_class IN ('SYNTHETIC_TEST','APPROVED_REFERENCE')", name="ck_reference_class"
        ),
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(unique=True, nullable=False)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"), nullable=False
    )
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    split: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    lineage_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_reference: Mapped[str | None] = mapped_column(String(200))


class EvaluationExaminerQualification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_examiner_qualifications"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING_APPROVAL','APPROVED')", name="ck_examiner_state"),
        CheckConstraint(
            "approved_by IS NULL OR (approved_by <> examiner_id AND approved_by <> created_by)",
            name="ck_examiner_independent",
        ),
        CheckConstraint(
            "evidence_class IN ('SYNTHETIC_TEST','VERIFIED_CREDENTIAL')", name="ck_examiner_class"
        ),
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(unique=True, nullable=False)
    examiner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT")
    )
    body: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(30), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_reference: Mapped[str | None] = mapped_column(String(200))


class EvaluationExpertJudgment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_expert_judgments"
    __table_args__ = (
        UniqueConstraint("reference_id", "examiner_id", name="uq_expert_independent_judgment"),
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(unique=True, nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_references.id", ondelete="RESTRICT"), nullable=False
    )
    reference_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    examiner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    qualification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_examiner_qualifications.id", ondelete="RESTRICT"), nullable=False
    )
    qualification_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvaluationAdjudication(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_adjudications"
    __table_args__ = (
        UniqueConstraint("reference_id", "version_number", name="uq_adjudication_version"),
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(unique=True, nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_references.id", ondelete="RESTRICT"), nullable=False
    )
    reference_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    version_number: Mapped[int] = mapped_column(nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evaluation_adjudications.id", ondelete="RESTRICT")
    )
    adjudicator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    qualification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_examiner_qualifications.id", ondelete="RESTRICT"), nullable=False
    )
    body: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


REFERENCE_MODELS = (
    EvaluationReference,
    EvaluationExaminerQualification,
    EvaluationExpertJudgment,
    EvaluationAdjudication,
)


def _protect_references(session: Session) -> None:
    for row in session.deleted:
        if isinstance(row, REFERENCE_MODELS):
            raise ValueError("EVALUATION_REFERENCE_HISTORY_IMMUTABLE")
    for row in list(session.identity_map.values()):
        if not isinstance(row, REFERENCE_MODELS) or not getattr(inspect(row), "persistent", False):
            continue
        table = row.__table__
        old = (
            session.connection().execute(select(table).where(table.c.id == row.id)).mappings().one()
        )
        changed = {c.name for c in table.c if old[c.name] != getattr(row, c.name)}
        allowed: set[str] = set()
        if (
            isinstance(row, (EvaluationReference, EvaluationExaminerQualification))
            and old["status"] == "PENDING_APPROVAL"
            and row.status == "APPROVED"
        ):
            allowed = {"status", "approved_at", "approved_by", "approval_reference"}
        if changed - allowed:
            raise ValueError("EVALUATION_REFERENCE_HISTORY_IMMUTABLE")


event.listen(Session, "before_flush", lambda session, *_: _protect_references(session))
event.listen(Session, "before_commit", _protect_references)
