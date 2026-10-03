from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import AssessmentClassification, ExamOriginType, ExamVersionStatus
from app.models.identity import enum_type


class Exam(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "exams"
    __table_args__ = (
        UniqueConstraint("code", "organisation_id", name="uq_exam_scope_code"),
        CheckConstraint(
            "(scope_type = 'PLATFORM_SHARED' AND organisation_id IS NULL) OR "
            "(scope_type = 'ORGANISATION_PRIVATE' AND organisation_id IS NOT NULL)",
            name="ck_exam_scope",
        ),
        Index("ix_exam_scope", "scope_type", "organisation_id"),
    )

    code: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(1000))
    scope_type: Mapped[str] = mapped_column(String(40), nullable=False)
    organisation_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("organisations.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    versions: Mapped[list[ExamVersion]] = relationship(
        back_populates="exam", cascade="all, delete-orphan"
    )


class ExamVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "exam_versions"
    __table_args__ = (
        UniqueConstraint("exam_id", "version_label", name="uq_exam_version_label"),
        CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_exam_version_effective_dates",
        ),
        Index("ix_exam_version_resolution", "status", "effective_from", "effective_until"),
    )

    exam_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exams.id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[ExamVersionStatus] = mapped_column(enum_type(ExamVersionStatus), nullable=False)
    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    classification: Mapped[AssessmentClassification] = mapped_column(
        enum_type(AssessmentClassification), nullable=False
    )
    origin_type: Mapped[ExamOriginType] = mapped_column(enum_type(ExamOriginType), nullable=False)
    proposed_by_ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_runs.id"))
    proposed_by_ai_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ai_run_steps.id")
    )
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    supersedes_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="SET NULL")
    )
    timing_override: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    navigation_override: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
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

    exam: Mapped[Exam] = relationship(back_populates="versions")
    modules: Mapped[list[ExamVersionModule]] = relationship(
        back_populates="exam_version",
        cascade="all, delete-orphan",
        order_by="ExamVersionModule.ordinal",
    )
    supersedes_version: Mapped[Optional[ExamVersion]] = relationship(
        remote_side="ExamVersion.id", uselist=False
    )


class ExamVersionModule(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "exam_version_modules"
    __table_args__ = (
        UniqueConstraint("exam_version_id", "standard_pack_module_id", name="uq_exam_module_ref"),
    )
    exam_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="CASCADE"), nullable=False
    )
    standard_pack_module_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_modules.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    exam_version: Mapped[ExamVersion] = relationship(back_populates="modules")
    sections: Mapped[list[ExamVersionSection]] = relationship(
        back_populates="module", cascade="all, delete-orphan", order_by="ExamVersionSection.ordinal"
    )


class ExamVersionSection(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "exam_version_sections"
    __table_args__ = (
        UniqueConstraint(
            "exam_version_module_id", "standard_pack_section_id", name="uq_exam_section_ref"
        ),
    )
    exam_version_module_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_version_modules.id", ondelete="CASCADE"), nullable=False
    )
    standard_pack_section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_sections.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    module: Mapped[ExamVersionModule] = relationship(back_populates="sections")
    tasks: Mapped[list[ExamVersionTask]] = relationship(
        back_populates="section", cascade="all, delete-orphan", order_by="ExamVersionTask.ordinal"
    )


class ExamVersionTask(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "exam_version_tasks"
    __table_args__ = (
        UniqueConstraint(
            "exam_version_section_id", "standard_pack_task_spec_id", name="uq_exam_task_ref"
        ),
    )
    exam_version_section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_version_sections.id", ondelete="CASCADE"), nullable=False
    )
    standard_pack_task_spec_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_task_specs.id", ondelete="RESTRICT"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[ExamVersionSection] = relationship(back_populates="tasks")
    item_bindings: Mapped[list[ExamVersionItemBinding]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="ExamVersionItemBinding.slot_index",
    )


class ExamVersionItemBinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "exam_version_item_bindings"
    __table_args__ = (
        UniqueConstraint("exam_version_task_id", "slot_index", name="uq_exam_item_slot"),
        CheckConstraint("slot_index >= 0", name="ck_exam_item_slot_nonnegative"),
    )
    exam_version_task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_version_tasks.id", ondelete="CASCADE"), nullable=False
    )
    slot_index: Mapped[int] = mapped_column(Integer, nullable=False)
    question_bank_item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id", ondelete="RESTRICT"), nullable=False
    )
    origin_type: Mapped[ExamOriginType] = mapped_column(enum_type(ExamOriginType), nullable=False)
    proposed_by_ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_runs.id"))
    proposed_by_ai_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ai_run_steps.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    task: Mapped[ExamVersionTask] = relationship(back_populates="item_bindings")
