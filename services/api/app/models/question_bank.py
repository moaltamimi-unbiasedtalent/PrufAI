from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AssessmentFamily,
    PackMediaRequirement,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankOriginType,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankSkill,
    QuestionBankTaskType,
)
from app.models.identity import enum_type


class QuestionBankItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "question_bank_items"
    __table_args__ = (
        UniqueConstraint("code", "organisation_id", name="uq_question_bank_item_scope_code"),
        CheckConstraint(
            "(scope_type = 'PLATFORM_SHARED' AND organisation_id IS NULL) OR "
            "(scope_type = 'ORGANISATION_PRIVATE' AND organisation_id IS NOT NULL)",
            name="ck_question_bank_item_scope",
        ),
        Index("ix_question_bank_item_scope_status", "scope_type", "organisation_id", "status"),
    )

    code: Mapped[str] = mapped_column(String(160), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(40), nullable=False)
    organisation_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("organisations.id"))
    status: Mapped[QuestionBankLifecycleStatus] = mapped_column(
        enum_type(QuestionBankLifecycleStatus),
        default=QuestionBankLifecycleStatus.DRAFT,
        nullable=False,
    )
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    versions: Mapped[list[QuestionBankItemVersion]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class QuestionBankItemVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "question_bank_item_versions"
    __table_args__ = (
        UniqueConstraint("item_id", "version_label", name="uq_question_bank_item_version"),
        CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_question_bank_effective_dates",
        ),
        CheckConstraint("max_points IS NULL OR max_points >= 0", name="ck_question_bank_points"),
        Index("ix_question_bank_version_resolution", "status", "assessment_family", "language"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_items.id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[QuestionBankLifecycleStatus] = mapped_column(
        enum_type(QuestionBankLifecycleStatus),
        default=QuestionBankLifecycleStatus.DRAFT,
        nullable=False,
    )
    assessment_family: Mapped[AssessmentFamily] = mapped_column(
        enum_type(AssessmentFamily), nullable=False
    )
    exam_family: Mapped[Optional[str]] = mapped_column(String(120))
    language: Mapped[Optional[str]] = mapped_column(String(20))
    skill: Mapped[Optional[QuestionBankSkill]] = mapped_column(enum_type(QuestionBankSkill))
    task_type: Mapped[QuestionBankTaskType] = mapped_column(
        enum_type(QuestionBankTaskType), nullable=False
    )
    response_mode: Mapped[PackResponseMode] = mapped_column(
        enum_type(PackResponseMode), nullable=False
    )
    media_requirement: Mapped[PackMediaRequirement] = mapped_column(
        enum_type(PackMediaRequirement), default=PackMediaRequirement.NONE, nullable=False
    )
    difficulty_label: Mapped[Optional[str]] = mapped_column(String(40))
    content_schema_version: Mapped[str] = mapped_column(String(40), default="1", nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    scoring_policy: Mapped[QuestionBankScoringPolicy] = mapped_column(
        enum_type(QuestionBankScoringPolicy), nullable=False
    )
    max_points: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    scoring_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    accessibility: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    rights_status: Mapped[QuestionBankRightsStatus] = mapped_column(
        enum_type(QuestionBankRightsStatus), nullable=False
    )
    rights_holder: Mapped[Optional[str]] = mapped_column(String(240))
    licence_reference: Mapped[Optional[str]] = mapped_column(String(240))
    permitted_use: Mapped[Optional[str]] = mapped_column(String(500))
    rights_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rights_notes: Mapped[Optional[str]] = mapped_column(Text)
    rubric_reference: Mapped[Optional[str]] = mapped_column(String(240))
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    supersedes_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("question_bank_item_versions.id", ondelete="SET NULL")
    )
    origin_type: Mapped[QuestionBankOriginType] = mapped_column(
        enum_type(QuestionBankOriginType), nullable=False
    )
    proposed_by_ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_runs.id"))
    proposed_by_ai_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ai_run_steps.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    item: Mapped[QuestionBankItem] = relationship(back_populates="versions")


class QuestionBankItemCefrLevel(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "question_bank_item_cefr_levels"
    __table_args__ = (
        UniqueConstraint("item_version_id", "cefr_level", name="uq_question_bank_cefr"),
        CheckConstraint(
            "cefr_level IN ('A1','A2','B1','B2','C1','C2')", name="ck_question_bank_cefr"
        ),
    )
    item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id", ondelete="CASCADE"), nullable=False
    )
    cefr_level: Mapped[str] = mapped_column(String(2), nullable=False)


class QuestionBankItemEvidenceBinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "question_bank_item_evidence_bindings"
    __table_args__ = (
        CheckConstraint(
            "knowledge_document_version_id IS NOT NULL OR knowledge_page_id IS NOT NULL "
            "OR knowledge_chunk_id IS NOT NULL OR evidence_id IS NOT NULL",
            name="ck_question_bank_evidence_reference",
        ),
    )
    item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("knowledge_document_versions.id")
    )
    knowledge_page_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("knowledge_document_pages.id")
    )
    knowledge_chunk_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("knowledge_chunks.id")
    )
    evidence_id: Mapped[Optional[str]] = mapped_column(String(240))
    purpose: Mapped[str] = mapped_column(String(80), nullable=False)


class QuestionBankStandardPackBinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "question_bank_standard_pack_bindings"
    __table_args__ = (
        UniqueConstraint(
            "item_version_id",
            "standard_pack_version_id",
            "task_spec_id",
            name="uq_question_bank_pack_binding",
        ),
    )
    item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id", ondelete="CASCADE"), nullable=False
    )
    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    task_spec_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("standard_pack_task_specs.id", ondelete="RESTRICT")
    )
    validation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuestionBankMediaAsset(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "question_bank_media_assets"
    item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id", ondelete="CASCADE"), nullable=False
    )
    asset_type: Mapped[PackMediaRequirement] = mapped_column(
        enum_type(PackMediaRequirement), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    rights_reference: Mapped[Optional[str]] = mapped_column(String(240))
    duration_seconds: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    dimensions: Mapped[dict[str, int]] = mapped_column(JSON, default=dict, nullable=False)
