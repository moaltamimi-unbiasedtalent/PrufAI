"""Governed evaluation history, separate from candidate assessment authority."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    inspect,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin

REGRESSION_ORIGINS = frozenset({"DYNAMIC_REGRESSION_CASE", "DYNAMIC_REGRESSION_SUITE"})
REGRESSION_ADAPTER = "GOVERNED_REGRESSION_COMPARISON_V1"


class EvaluationDataset(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_datasets"
    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvaluationDatasetVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_number", name="uq_evaluation_dataset_version"),
        CheckConstraint("version_number > 0", name="ck_evaluation_dataset_version_positive"),
        CheckConstraint(
            "status IN ('DRAFT','REVIEW_REQUIRED','APPROVED','PUBLISHED','REJECTED')",
            name="ck_evaluation_dataset_state",
        ),
        CheckConstraint(
            "approved_by IS NULL OR approved_by <> created_by",
            name="ck_evaluation_dataset_independent_approval",
        ),
        CheckConstraint(
            "(status = 'REJECTED' AND rejected_by IS NOT NULL AND rejected_by <> created_by "
            "AND rejected_at IS NOT NULL AND rejection_review_reference IS NOT NULL) OR "
            "(status <> 'REJECTED' AND rejected_by IS NULL AND rejected_at IS NULL "
            "AND rejection_review_reference IS NULL)",
            name="ck_evaluation_dataset_rejection",
        ),
        Index("ix_evaluation_dataset_status", "status", "created_at"),
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    assessment_family: Mapped[str] = mapped_column(String(40), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    cefr_level: Mapped[str | None] = mapped_column(String(10))
    skill: Mapped[str] = mapped_column(String(30), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    cases: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(30), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provenance_review_reference: Mapped[str | None] = mapped_column(String(200))
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_review_reference: Mapped[str | None] = mapped_column(String(200))
    published_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING','COMPLETED','FAILED')", name="ck_evaluation_run_state"
        ),
        CheckConstraint("hard_gate IN ('PENDING','PASS','FAIL')", name="ck_evaluation_run_gate"),
        Index("ix_evaluation_runs_dataset_created", "dataset_version_id", "created_at"),
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(unique=True, nullable=False)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_dataset_versions.id", ondelete="RESTRICT"), nullable=False
    )
    dataset_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    request_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    configuration_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    execution_class: Mapped[str] = mapped_column(String(30), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    hard_gate: Mapped[str] = mapped_column(String(20), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationCaseResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (UniqueConstraint("run_id", "case_id", name="uq_evaluation_case_result"),)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(String(120), nullable=False)
    input_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_reference: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvaluationReview(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evaluation_reviews"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('ACCEPTED','REJECTED','CHANGES_REQUIRED')",
            name="ck_evaluation_review_decision",
        ),
        CheckConstraint(
            "(operation_id IS NULL) = (request_checksum IS NULL)",
            name="ck_evaluation_review_operation",
        ),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    run_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(unique=True)
    request_checksum: Mapped[str | None] = mapped_column(String(64))
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    review_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvaluationThresholdDefinition(UUIDPrimaryKeyMixin, Base):
    """One immutable proposal and, if approved, its sealed approval receipt."""

    __tablename__ = "evaluation_threshold_definitions"
    __table_args__ = (
        CheckConstraint(
            "(status = 'DRAFT' AND approval IS NULL AND approved_by IS NULL "
            "AND approved_at IS NULL) OR "
            "(status = 'APPROVED' AND approval IS NOT NULL AND approved_by IS NOT NULL "
            "AND approved_at IS NOT NULL "
            "AND approved_by <> created_by)",
            name="ck_evaluation_threshold_approval",
        ),
    )

    operation_id: Mapped[uuid.UUID] = mapped_column(unique=True, nullable=False)
    suite_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_dataset_versions.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    proposal_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    approval: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


VERSION_TRANSITIONS = {
    "DRAFT": ("REVIEW_REQUIRED", {"status", "submitted_at"}),
    "REVIEW_REQUIRED": (
        "APPROVED",
        {"status", "approved_at", "approved_by", "provenance_review_reference"},
    ),
    "APPROVED": ("PUBLISHED", {"status", "published_at", "published_by"}),
}
RUN_MUTABLE = {
    "status",
    "hard_gate",
    "metrics",
    "limitations",
    "failure_count",
    "checksum",
    "completed_at",
}
EVALUATION_MODELS = (
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationRun,
    EvaluationCaseResult,
    EvaluationReview,
    EvaluationThresholdDefinition,
)


def _protect_evaluation(session: Session) -> None:
    for row in list(session.deleted):
        if isinstance(row, EVALUATION_MODELS):
            raise ValueError("EVALUATION_HISTORY_IMMUTABLE")
    # Raw column reads also catch untracked nested JSON edits before commit.
    for row in list(session.identity_map.values()):
        if not isinstance(row, EVALUATION_MODELS) or not getattr(inspect(row), "persistent", False):
            continue
        table = row.__table__
        old = (
            session.connection().execute(select(table).where(table.c.id == row.id)).mappings().one()
        )
        changed = {
            column.name for column in table.c if old[column.name] != getattr(row, column.name)
        }
        if not changed:
            continue
        allowed: set[str] = set()
        if isinstance(row, EvaluationDatasetVersion):
            transition = VERSION_TRANSITIONS.get(old["status"])
            if transition and row.status == transition[0]:
                allowed = transition[1]
            elif (
                old["status"] == "REVIEW_REQUIRED"
                and row.status == "REJECTED"
                and old["provenance"].get("origin") in REGRESSION_ORIGINS
            ):
                allowed = {"status", "rejected_by", "rejected_at", "rejection_review_reference"}
        elif (
            isinstance(row, EvaluationThresholdDefinition)
            and old["status"] == "DRAFT"
            and row.status == "APPROVED"
        ):
            allowed = {"status", "approved_by", "approved_at", "approval", "checksum"}
        elif isinstance(row, EvaluationRun) and old["status"] == "RUNNING":
            allowed = RUN_MUTABLE
        if changed - allowed:
            raise ValueError("EVALUATION_HISTORY_IMMUTABLE")


event.listen(Session, "before_flush", lambda session, *_: _protect_evaluation(session))
event.listen(Session, "before_commit", _protect_evaluation)
