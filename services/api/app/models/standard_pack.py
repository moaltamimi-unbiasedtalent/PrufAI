from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AssessmentFamily,
    PackMediaRequirement,
    PackNavigationMode,
    PackResponseMode,
    StandardPackBindingType,
    StandardPackStatus,
)
from app.models.identity import enum_type


class StandardPack(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "standard_packs"
    __table_args__ = (
        UniqueConstraint("code", name="uq_standard_pack_code"),
        Index("ix_standard_packs_family_status", "assessment_family", "status"),
    )

    code: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    assessment_family: Mapped[AssessmentFamily] = mapped_column(
        enum_type(AssessmentFamily), nullable=False
    )
    authority_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("knowledge_authorities.id", ondelete="RESTRICT")
    )
    exam_family: Mapped[Optional[str]] = mapped_column(String(120))
    language: Mapped[Optional[str]] = mapped_column(String(20))
    status: Mapped[StandardPackStatus] = mapped_column(
        enum_type(StandardPackStatus), default=StandardPackStatus.DRAFT, nullable=False
    )
    description: Mapped[Optional[str]] = mapped_column(Text)

    versions: Mapped[list[StandardPackVersion]] = relationship(
        back_populates="standard_pack", cascade="all, delete-orphan"
    )


class StandardPackVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "standard_pack_versions"
    __table_args__ = (
        UniqueConstraint(
            "standard_pack_id", "version_label", name="uq_standard_pack_version_label"
        ),
        Index(
            "ix_standard_pack_versions_status_dates", "status", "effective_from", "effective_until"
        ),
        CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_standard_pack_version_effective_dates",
        ),
    )

    standard_pack_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_packs.id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[StandardPackStatus] = mapped_column(
        enum_type(StandardPackStatus), default=StandardPackStatus.DRAFT, nullable=False
    )
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    supersedes_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="SET NULL")
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)
    configuration_schema_version: Mapped[str] = mapped_column(
        String(40), default="1", nullable=False
    )
    configuration_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    scoring_configuration_ref: Mapped[Optional[str]] = mapped_column(String(240))
    result_configuration_ref: Mapped[Optional[str]] = mapped_column(String(240))
    rubric_set_ref: Mapped[Optional[str]] = mapped_column(String(240))
    supported_levels: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_classifications: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    standard_pack: Mapped[StandardPack] = relationship(back_populates="versions")
    supersedes_version: Mapped[Optional[StandardPackVersion]] = relationship(
        remote_side="StandardPackVersion.id", uselist=False
    )
    source_bindings: Mapped[list[StandardPackSourceBinding]] = relationship(
        back_populates="pack_version", cascade="all, delete-orphan"
    )
    modules: Mapped[list[StandardPackModule]] = relationship(
        back_populates="pack_version",
        cascade="all, delete-orphan",
        order_by="StandardPackModule.sequence",
    )
    rule_bindings: Mapped[list[Any]] = relationship(
        "StandardPackRuleBinding",
        back_populates="pack_version",
        cascade="all, delete-orphan",
        order_by="StandardPackRuleBinding.sequence",
    )


class StandardPackSourceBinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "standard_pack_source_bindings"
    __table_args__ = (
        Index("ix_standard_pack_bindings_version", "standard_pack_version_id"),
        CheckConstraint(
            "knowledge_source_id IS NOT NULL OR document_version_id IS NOT NULL",
            name="ck_standard_pack_binding_reference",
        ),
    )

    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="RESTRICT")
    )
    document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="RESTRICT")
    )
    document_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    binding_type: Mapped[StandardPackBindingType] = mapped_column(
        enum_type(StandardPackBindingType), nullable=False
    )
    purpose: Mapped[Optional[str]] = mapped_column(String(500))
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pack_version: Mapped[StandardPackVersion] = relationship(back_populates="source_bindings")


class StandardPackModule(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "standard_pack_modules"
    __table_args__ = (
        UniqueConstraint("standard_pack_version_id", "code", name="uq_standard_pack_module_code"),
        UniqueConstraint(
            "standard_pack_version_id", "sequence", name="uq_standard_pack_module_sequence"
        ),
        CheckConstraint(
            "time_limit_seconds IS NULL OR time_limit_seconds >= 0", name="ck_pack_module_time"
        ),
    )

    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    skill: Mapped[Optional[str]] = mapped_column(String(80))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    time_limit_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    preparation_time_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    navigation_mode: Mapped[PackNavigationMode] = mapped_column(
        enum_type(PackNavigationMode), default=PackNavigationMode.LINEAR, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pack_version: Mapped[StandardPackVersion] = relationship(back_populates="modules")
    sections: Mapped[list[StandardPackSection]] = relationship(
        back_populates="module",
        cascade="all, delete-orphan",
        order_by="StandardPackSection.sequence",
    )


class StandardPackSection(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "standard_pack_sections"
    __table_args__ = (
        UniqueConstraint("module_id", "code", name="uq_standard_pack_section_code"),
        UniqueConstraint("module_id", "sequence", name="uq_standard_pack_section_sequence"),
        CheckConstraint(
            "time_limit_seconds IS NULL OR time_limit_seconds >= 0", name="ck_pack_section_time"
        ),
    )

    module_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_modules.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    instructions_ref: Mapped[Optional[str]] = mapped_column(String(240))
    time_limit_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    navigation_mode: Mapped[PackNavigationMode] = mapped_column(
        enum_type(PackNavigationMode), default=PackNavigationMode.LINEAR, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    module: Mapped[StandardPackModule] = relationship(back_populates="sections")
    tasks: Mapped[list[StandardPackTaskSpecification]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="StandardPackTaskSpecification.sequence",
    )


class StandardPackTaskSpecification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "standard_pack_task_specs"
    __table_args__ = (
        UniqueConstraint("section_id", "code", name="uq_standard_pack_task_code"),
        UniqueConstraint("section_id", "sequence", name="uq_standard_pack_task_sequence"),
        CheckConstraint(
            "min_items >= 0 AND max_items >= min_items", name="ck_pack_task_item_range"
        ),
    )

    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_sections.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    task_type: Mapped[str] = mapped_column(String(60), nullable=False)
    min_items: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_items: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    required_items: Mapped[Optional[int]] = mapped_column(Integer)
    points_per_item: Mapped[Optional[int]] = mapped_column(Integer)
    response_mode: Mapped[PackResponseMode] = mapped_column(
        enum_type(PackResponseMode), nullable=False
    )
    media_requirement: Mapped[PackMediaRequirement] = mapped_column(
        enum_type(PackMediaRequirement), default=PackMediaRequirement.NONE, nullable=False
    )
    generation_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    selection_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rubric_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rubric_reference: Mapped[Optional[str]] = mapped_column(String(240))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    section: Mapped[StandardPackSection] = relationship(back_populates="tasks")
