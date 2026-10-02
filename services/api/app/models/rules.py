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
    RuleCategory,
    RuleEvidenceType,
    RuleSeverity,
    RuleStatus,
)
from app.models.identity import enum_type


class RuleDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rule_definitions"

    code: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[RuleCategory] = mapped_column(enum_type(RuleCategory), nullable=False)
    status: Mapped[RuleStatus] = mapped_column(
        enum_type(RuleStatus), default=RuleStatus.DRAFT, nullable=False
    )
    versions: Mapped[list[RuleVersion]] = relationship(
        back_populates="definition", cascade="all, delete-orphan"
    )


class RuleVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "rule_versions"
    __table_args__ = (
        UniqueConstraint("rule_definition_id", "version_label", name="uq_rule_version_label"),
        Index("ix_rule_versions_status_dates", "status", "effective_from", "effective_until"),
        CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_rule_version_effective_dates",
        ),
    )

    rule_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rule_definitions.id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(120), nullable=False)
    expression_schema_version: Mapped[str] = mapped_column(String(40), default="1", nullable=False)
    expression: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    severity: Mapped[RuleSeverity] = mapped_column(enum_type(RuleSeverity), nullable=False)
    failure_code: Mapped[str] = mapped_column(String(120), nullable=False)
    failure_message_template: Mapped[str] = mapped_column(String(500), nullable=False)
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[RuleStatus] = mapped_column(
        enum_type(RuleStatus), default=RuleStatus.DRAFT, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    supersedes_rule_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("rule_versions.id", ondelete="SET NULL")
    )
    checksum: Mapped[Optional[str]] = mapped_column(String(64))
    internal_policy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    test_cases: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)

    definition: Mapped[RuleDefinition] = relationship(back_populates="versions")
    evidence_bindings: Mapped[list[RuleEvidenceBinding]] = relationship(
        back_populates="rule_version", cascade="all, delete-orphan"
    )
    pack_bindings: Mapped[list[StandardPackRuleBinding]] = relationship(
        back_populates="rule_version", cascade="all, delete-orphan"
    )


class RuleEvidenceBinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "rule_evidence_bindings"
    __table_args__ = (
        Index("ix_rule_evidence_rule_version", "rule_version_id"),
        CheckConstraint(
            "knowledge_document_version_id IS NOT NULL",
            name="ck_rule_evidence_document_required",
        ),
    )

    rule_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rule_versions.id", ondelete="CASCADE"), nullable=False
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
    evidence_type: Mapped[RuleEvidenceType] = mapped_column(
        enum_type(RuleEvidenceType), nullable=False
    )
    document_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    rule_version: Mapped[RuleVersion] = relationship(back_populates="evidence_bindings")


class StandardPackRuleBinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "standard_pack_rule_bindings"
    __table_args__ = (
        UniqueConstraint(
            "standard_pack_version_id", "rule_version_id", name="uq_pack_rule_version_binding"
        ),
        Index("ix_pack_rule_bindings_pack", "standard_pack_version_id"),
    )

    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="CASCADE"), nullable=False
    )
    rule_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rule_versions.id", ondelete="RESTRICT"), nullable=False
    )
    scope: Mapped[Optional[str]] = mapped_column(String(80))
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pack_version: Mapped[Any] = relationship(
        "StandardPackVersion",
        back_populates="rule_bindings",
    )
    rule_version: Mapped[RuleVersion] = relationship(back_populates="pack_bindings")


class RuleSet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rule_sets"

    code: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[RuleStatus] = mapped_column(
        enum_type(RuleStatus), default=RuleStatus.DRAFT, nullable=False
    )
