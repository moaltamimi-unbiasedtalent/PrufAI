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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    MemoryCategory,
    MemoryLifecycleStatus,
    MemoryOriginType,
    MemoryScopeType,
)
from app.models.identity import enum_type


class LongTermMemory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "long_term_memories"
    __table_args__ = (UniqueConstraint("code", name="uq_long_term_memory_code"),)

    code: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    category: Mapped[MemoryCategory] = mapped_column(enum_type(MemoryCategory), nullable=False)
    scope_type: Mapped[MemoryScopeType] = mapped_column(enum_type(MemoryScopeType), nullable=False)
    organisation_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("organisations.id"))
    description: Mapped[Optional[str]] = mapped_column(Text)
    versions: Mapped[list[LongTermMemoryVersion]] = relationship(
        back_populates="memory", cascade="all, delete-orphan"
    )


class LongTermMemoryVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "long_term_memory_versions"
    __table_args__ = (
        UniqueConstraint("memory_id", "version_label", name="uq_long_term_memory_version"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_memory_effective_window",
        ),
        Index("ix_memory_version_resolution", "status", "effective_from", "expires_at"),
    )

    memory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("long_term_memories.id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_schema_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1")
    status: Mapped[MemoryLifecycleStatus] = mapped_column(
        enum_type(MemoryLifecycleStatus), nullable=False
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    review_after: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    supersedes_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("long_term_memory_versions.id")
    )
    origin_type: Mapped[MemoryOriginType] = mapped_column(
        enum_type(MemoryOriginType), nullable=False
    )
    proposed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    proposed_by_ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_runs.id"))
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    memory: Mapped[LongTermMemory] = relationship(back_populates="versions")


class MemoryEvidenceBinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "memory_evidence_bindings"
    __table_args__ = (
        CheckConstraint(
            "knowledge_document_version_id IS NOT NULL OR standard_pack_version_id IS NOT NULL "
            "OR rule_version_id IS NOT NULL OR ai_run_id IS NOT NULL",
            name="ck_memory_evidence_reference",
        ),
    )
    memory_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("long_term_memory_versions.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("knowledge_document_versions.id")
    )
    standard_pack_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("standard_pack_versions.id")
    )
    rule_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("rule_versions.id"))
    ai_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_runs.id"))
    evidence_type: Mapped[str] = mapped_column(String(80), nullable=False, default="SUPPORTING")


class AIRunMemoryReference(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_run_memory_references"
    __table_args__ = (
        UniqueConstraint("ai_run_id", "memory_version_id", name="uq_ai_run_memory_reference"),
    )
    ai_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=False
    )
    memory_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("long_term_memory_versions.id", ondelete="RESTRICT"), nullable=False
    )
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    context_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
