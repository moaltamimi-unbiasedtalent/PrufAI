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
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import (
    AssessmentFamily,
    QuestionBankSkill,
    RubricVersionStatus,
    SubjectiveAssessmentStatus,
    SubjectiveConfidence,
    SubjectiveCriterionStatus,
)
from app.models.identity import enum_type


class AssessmentRubric(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "assessment_rubrics"
    __table_args__ = (
        UniqueConstraint("code", "organisation_id", name="uq_assessment_rubric_scope_code"),
    )

    code: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    organisation_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("organisations.id"))
    status: Mapped[RubricVersionStatus] = mapped_column(
        enum_type(RubricVersionStatus), default=RubricVersionStatus.DRAFT, nullable=False
    )
    versions: Mapped[list[RubricVersion]] = relationship(
        back_populates="rubric",
        cascade="all, delete-orphan",
        order_by="RubricVersion.version_label",
    )


class RubricVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "rubric_versions"
    __table_args__ = (
        UniqueConstraint("rubric_id", "version_label", name="uq_rubric_version_label"),
        Index("ix_rubric_versions_status", "status"),
    )

    rubric_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assessment_rubrics.id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    assessment_family: Mapped[AssessmentFamily] = mapped_column(
        enum_type(AssessmentFamily), nullable=False
    )
    language: Mapped[Optional[str]] = mapped_column(String(20))
    cefr_levels: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    skill: Mapped[QuestionBankSkill] = mapped_column(enum_type(QuestionBankSkill), nullable=False)
    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    task_spec_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("standard_pack_task_specs.id", ondelete="RESTRICT")
    )
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    performance_levels: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    aggregation_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ai_assessment_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    human_review_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[RubricVersionStatus] = mapped_column(
        enum_type(RubricVersionStatus), default=RubricVersionStatus.DRAFT, nullable=False
    )
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("rubric_versions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))

    rubric: Mapped[AssessmentRubric] = relationship(back_populates="versions")
    evidence_bindings: Mapped[list[RubricEvidenceBinding]] = relationship(
        back_populates="rubric_version", cascade="all, delete-orphan"
    )


class RubricEvidenceBinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "rubric_evidence_bindings"
    __table_args__ = (Index("ix_rubric_evidence_version", "rubric_version_id"),)

    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_versions.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="RESTRICT"), nullable=False
    )
    knowledge_page_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("knowledge_document_pages.id", ondelete="RESTRICT")
    )
    knowledge_chunk_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="RESTRICT")
    )
    reference: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    purpose: Mapped[str] = mapped_column(String(120), nullable=False)
    source_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    required: Mapped[bool] = mapped_column(default=True, nullable=False)

    rubric_version: Mapped[RubricVersion] = relationship(back_populates="evidence_bindings")


class SubjectiveAssessment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "subjective_assessments"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id", "exam_version_item_binding_id", name="uq_subjective_assessment_item"
        ),
        Index("ix_subjective_assessment_org", "organisation_id"),
    )

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    exam_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), nullable=False
    )
    exam_version_item_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_version_item_bindings.id", ondelete="RESTRICT"), nullable=False
    )
    question_bank_item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    versions: Mapped[list[SubjectiveAssessmentVersion]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="SubjectiveAssessmentVersion.version_number",
    )


class SubjectiveAssessmentVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "subjective_assessment_versions"
    __table_args__ = (
        UniqueConstraint(
            "subjective_assessment_id", "version_number", name="uq_subjective_assessment_version"
        ),
        Index("ix_subjective_assessment_version_status", "status"),
    )

    subjective_assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subjective_assessments.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id"), nullable=False
    )
    exam_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_versions.id"), nullable=False
    )
    exam_version_item_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_version_item_bindings.id"), nullable=False
    )
    question_bank_item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id"), nullable=False
    )
    candidate_response_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_responses.id"), nullable=False
    )
    candidate_response_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_response_revisions.id"), nullable=False
    )
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_versions.id"), nullable=False
    )
    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id"), nullable=False
    )
    status: Mapped[SubjectiveAssessmentStatus] = mapped_column(
        enum_type(SubjectiveAssessmentStatus), nullable=False
    )
    response_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    rubric_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ai_workflow_versions.id")
    )
    agent_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("agent_versions.id"))
    prompt_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("prompt_versions.id"))
    model_profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ai_model_profiles.id")
    )
    candidate_media_asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("candidate_media_assets.id", ondelete="RESTRICT")
    )
    audio_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    speech_transcription_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("speech_transcription_versions.id", ondelete="RESTRICT")
    )
    transcript_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    evidence_mode_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    ai_proposal: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    human_approved_values: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    consistency_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="SET NULL")
    )
    proposed_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    approved_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    confidence_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    disagreement_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supersedes_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("subjective_assessment_versions.id", ondelete="SET NULL")
    )

    assessment: Mapped[SubjectiveAssessment] = relationship(back_populates="versions")
    criterion_assessments: Mapped[list[SubjectiveCriterionAssessment]] = relationship(
        back_populates="assessment_version",
        cascade="all, delete-orphan",
        order_by="SubjectiveCriterionAssessment.criterion_code",
    )


class SubjectiveCriterionAssessment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "subjective_criterion_assessments"
    __table_args__ = (
        UniqueConstraint(
            "subjective_assessment_version_id",
            "criterion_code",
            name="uq_subjective_criterion_version",
        ),
    )

    subjective_assessment_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subjective_assessment_versions.id", ondelete="CASCADE"), nullable=False
    )
    criterion_code: Mapped[str] = mapped_column(String(120), nullable=False)
    proposed_level: Mapped[str] = mapped_column(String(80), nullable=False)
    proposed_points: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    approved_level: Mapped[Optional[str]] = mapped_column(String(80))
    approved_points: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    confidence: Mapped[SubjectiveConfidence] = mapped_column(
        enum_type(SubjectiveConfidence), nullable=False
    )
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainties: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    disagreement: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ai_run_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_run_steps.id"))
    status: Mapped[SubjectiveCriterionStatus] = mapped_column(
        enum_type(SubjectiveCriterionStatus), nullable=False
    )

    assessment_version: Mapped[SubjectiveAssessmentVersion] = relationship(
        back_populates="criterion_assessments"
    )


@event.listens_for(SubjectiveAssessmentVersion, "before_update")
def _prevent_approved_subjective_mutation(
    _mapper: Mapper[SubjectiveAssessmentVersion],
    _connection: Any,
    target: SubjectiveAssessmentVersion,
) -> None:
    state = inspect(target)
    status_history = state.attrs.status.history
    old_status = status_history.deleted[0] if status_history.deleted else target.status
    if old_status not in {
        SubjectiveAssessmentStatus.APPROVED,
        SubjectiveAssessmentStatus.SUPERSEDED,
    }:
        return
    protected = (
        "attempt_id",
        "candidate_response_revision_id",
        "rubric_version_id",
        "response_checksum",
        "rubric_checksum",
        "candidate_media_asset_id",
        "audio_sha256",
        "speech_transcription_version_id",
        "transcript_checksum",
        "ai_proposal",
        "human_approved_values",
        "approved_score",
        "provenance",
        "checksum",
    )
    if any(state.attrs[name].history.has_changes() for name in protected):
        raise ValueError("approved subjective assessment versions are immutable")
