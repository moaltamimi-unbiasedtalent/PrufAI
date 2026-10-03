from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import ItemScoreStatus, ResultOutcome, ResultVersionStatus, ScoringRunStatus
from app.models.identity import enum_type


class ScoringRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "scoring_runs"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "response_set_checksum",
            "scoring_engine_version",
            "configuration_checksum",
            name="uq_scoring_run_idempotency",
        ),
        Index("ix_scoring_runs_status", "status"),
    )

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    exam_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), nullable=False
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[ScoringRunStatus] = mapped_column(enum_type(ScoringRunStatus), nullable=False)
    response_set_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    scoring_engine_name: Mapped[str] = mapped_column(String(120), nullable=False)
    scoring_engine_version: Mapped[str] = mapped_column(String(40), nullable=False)
    configuration_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failure_category: Mapped[Optional[str]] = mapped_column(String(120))
    initiated_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))

    snapshot: Mapped[Optional[ScoringResponseSnapshot]] = relationship(
        back_populates="scoring_run", uselist=False, cascade="all, delete-orphan"
    )
    item_scores: Mapped[list[ItemScore]] = relationship(
        back_populates="scoring_run", cascade="all, delete-orphan"
    )
    components: Mapped[list[ScoringComponent]] = relationship(
        back_populates="scoring_run", cascade="all, delete-orphan"
    )


class ScoringResponseSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "scoring_response_snapshots"
    __table_args__ = (UniqueConstraint("scoring_run_id", name="uq_scoring_snapshot_run"),)

    scoring_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    exam_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), nullable=False
    )
    runtime_plan_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    response_set_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    scoring_run: Mapped[ScoringRun] = relationship(back_populates="snapshot")


class ItemScore(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "item_scores"
    __table_args__ = (
        UniqueConstraint(
            "scoring_run_id", "exam_version_item_binding_id", name="uq_item_score_run_binding"
        ),
        Index("ix_item_scores_status", "status"),
    )

    scoring_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False
    )
    exam_version_item_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_version_item_bindings.id", ondelete="RESTRICT"), nullable=False
    )
    question_bank_item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_response_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("candidate_responses.id", ondelete="SET NULL")
    )
    candidate_response_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("candidate_response_revisions.id", ondelete="SET NULL")
    )
    scoring_policy: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[ItemScoreStatus] = mapped_column(enum_type(ItemScoreStatus), nullable=False)
    awarded_points: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    maximum_points: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    response_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    scoring_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    scoring_run: Mapped[ScoringRun] = relationship(back_populates="item_scores")


class ScoringComponent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "scoring_components"
    __table_args__ = (Index("ix_scoring_components_level", "level"),)

    scoring_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scoring_runs.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    source_entity_id: Mapped[str] = mapped_column(String(120), nullable=False)
    raw_awarded_points: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    raw_maximum_points: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    transformed_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    status: Mapped[ItemScoreStatus] = mapped_column(enum_type(ItemScoreStatus), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    scoring_run: Mapped[ScoringRun] = relationship(back_populates="components")


class AssessmentResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "assessment_results"
    __table_args__ = (UniqueConstraint("attempt_id", name="uq_result_attempt"),)

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    exam_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    versions: Mapped[list[ResultVersion]] = relationship(
        back_populates="result",
        cascade="all, delete-orphan",
        order_by="ResultVersion.version_number",
    )


class ResultVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "result_versions"
    __table_args__ = (
        UniqueConstraint("result_id", "version_number", name="uq_result_version_number"),
        Index("ix_result_versions_status", "status"),
    )

    result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_results.id", ondelete="CASCADE"), nullable=False
    )
    scoring_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scoring_runs.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ResultVersionStatus] = mapped_column(
        enum_type(ResultVersionStatus), nullable=False
    )
    response_set_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    scoring_run_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_awarded_points: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    raw_maximum_points: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    outcome: Mapped[Optional[ResultOutcome]] = mapped_column(enum_type(ResultOutcome))
    incomplete_reason: Mapped[Optional[str]] = mapped_column(String(240))
    rule_provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    released_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    supersedes_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("result_versions.id", ondelete="SET NULL")
    )

    result: Mapped[AssessmentResult] = relationship(back_populates="versions")
