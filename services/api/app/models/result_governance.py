from __future__ import annotations

# Several declarative foreign-key definitions are intentionally kept together.
# ruff: noqa: E501
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import (
    AppealDecision,
    AppealStatus,
    CorrectionStatus,
    CorrectionType,
    ModerationStatus,
)
from app.models.identity import enum_type


class ResultAppeal(UUIDPrimaryKeyMixin, Base):
    """Stable appeal identity; every material change is represented by a version."""

    __tablename__ = "result_appeals"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_result_appeal_operation"),
        Index("ix_result_appeals_queue", "organisation_id", "status", "created_at"),
        Index("ix_result_appeals_candidate", "candidate_user_id", "created_at"),
        Index("ix_result_appeals_result_version", "challenged_result_version_id"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id"), nullable=False
    )
    result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_results.id"), nullable=False
    )
    challenged_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("result_versions.id", ondelete="RESTRICT"), nullable=False
    )
    scoring_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scoring_runs.id"), nullable=False)
    exam_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_versions.id"), nullable=False
    )
    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id"), nullable=False
    )
    challenged_result_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AppealStatus] = mapped_column(enum_type(AppealStatus), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    current_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    corrected_result_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("result_versions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    versions: Mapped[list[ResultAppealVersion]] = relationship(
        back_populates="appeal",
        cascade="all, delete-orphan",
        order_by="ResultAppealVersion.version_number",
    )


class ResultAppealVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "result_appeal_versions"
    __table_args__ = (
        UniqueConstraint("appeal_id", "version_number", name="uq_result_appeal_version_number"),
        UniqueConstraint("operation_id", name="uq_result_appeal_version_operation"),
        Index("ix_result_appeal_versions_status", "status"),
    )

    appeal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("result_appeals.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AppealStatus] = mapped_column(enum_type(AppealStatus), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    grounds: Mapped[str] = mapped_column(Text, nullable=False)
    additional_information: Mapped[Optional[str]] = mapped_column(Text)
    challenged_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("result_versions.id"), nullable=False
    )
    challenged_result_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_bundle_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    decision: Mapped[Optional[AppealDecision]] = mapped_column(enum_type(AppealDecision))
    decision_reason: Mapped[Optional[str]] = mapped_column(Text)
    correction_category: Mapped[Optional[str]] = mapped_column(String(80))
    actor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    appeal: Mapped[ResultAppeal] = relationship(back_populates="versions")


class AppealInformationEntry(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "appeal_information_entries"
    __table_args__ = (Index("ix_appeal_information_appeal", "appeal_id", "created_at"),)

    appeal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("result_appeals.id", ondelete="CASCADE"), nullable=False
    )
    appeal_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("result_appeal_versions.id"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResultModerationCase(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "result_moderation_cases"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_result_moderation_operation"),
        Index("ix_result_moderation_queue", "organisation_id", "status", "created_at"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id"), nullable=False
    )
    result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_results.id"), nullable=False
    )
    result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("result_versions.id"), nullable=False
    )
    scoring_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scoring_runs.id"), nullable=False)
    result_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ModerationStatus] = mapped_column(enum_type(ModerationStatus), nullable=False)
    reason: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[str] = mapped_column(String(30), nullable=False, default="NORMAL")
    outcome: Mapped[Optional[str]] = mapped_column(String(40))
    operation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    assigned_to_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )


class ResultCorrection(UUIDPrimaryKeyMixin, Base):
    """Durable link from a human decision to new governed scoring artifacts."""

    __tablename__ = "result_corrections"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_result_correction_operation"),
        Index("ix_result_corrections_queue", "organisation_id", "status", "created_at"),
        Index("ix_result_corrections_appeal", "appeal_id"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    appeal_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("result_appeals.id"))
    appeal_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("result_appeal_versions.id")
    )
    moderation_case_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("result_moderation_cases.id")
    )
    source_result_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("result_versions.id"), nullable=False
    )
    source_result_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    correction_type: Mapped[CorrectionType] = mapped_column(
        enum_type(CorrectionType), nullable=False
    )
    status: Mapped[CorrectionStatus] = mapped_column(enum_type(CorrectionStatus), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(120), nullable=False)
    new_component_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("subjective_assessment_versions.id", ondelete="RESTRICT")
    )
    new_transcription_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("speech_transcription_versions.id", ondelete="RESTRICT")
    )
    new_scoring_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("scoring_runs.id"))
    new_result_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("result_versions.id")
    )
    failure_category: Mapped[Optional[str]] = mapped_column(String(120))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ResultCorrectionObjectiveBinding(UUIDPrimaryKeyMixin, Base):
    """Immutable governed replacement source for objective rescoring."""

    __tablename__ = "result_correction_objective_bindings"
    __table_args__ = (
        UniqueConstraint(
            "result_correction_id",
            "original_item_version_id",
            name="uq_result_correction_objective_source",
        ),
        Index("ix_result_correction_objective_correction", "result_correction_id"),
    )

    result_correction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("result_corrections.id", ondelete="CASCADE"), nullable=False
    )
    original_item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id"), nullable=False
    )
    replacement_item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id"), nullable=False
    )
    original_item_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    replacement_item_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
