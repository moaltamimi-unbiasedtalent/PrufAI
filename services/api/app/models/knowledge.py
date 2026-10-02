from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
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
    ExtractionStatus,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeIngestionStatus,
    KnowledgeScope,
    KnowledgeVersionStatus,
    LegalReviewStatus,
)
from app.models.identity import enum_type


class KnowledgeAuthority(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_authorities"

    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    authority_type: Mapped[KnowledgeAuthorityType] = mapped_column(
        enum_type(KnowledgeAuthorityType), nullable=False
    )
    jurisdiction: Mapped[Optional[str]] = mapped_column(String(120))
    website_url: Mapped[Optional[str]] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)

    sources: Mapped[list[KnowledgeSource]] = relationship(
        back_populates="authority", cascade="all, delete-orphan"
    )


class KnowledgeSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint("source_code", name="uq_knowledge_sources_code"),
        Index("ix_knowledge_sources_authority", "authority_id"),
        Index("ix_knowledge_sources_taxonomy", "assessment_family", "exam_family", "cefr_level"),
        CheckConstraint(
            "(assessment_family = 'LANGUAGE' AND "
            "(cefr_level IS NULL OR cefr_level IN "
            "('A1','A2','B1','B2','C1','C2'))) OR "
            "(assessment_family = 'INTEGRATION_KNOWLEDGE' AND cefr_level IS NULL)",
            name="ck_knowledge_source_taxonomy",
        ),
    )

    authority_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_authorities.id", ondelete="RESTRICT"), nullable=False
    )
    source_code: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    document_type: Mapped[KnowledgeDocumentType] = mapped_column(
        enum_type(KnowledgeDocumentType), nullable=False
    )
    scope: Mapped[KnowledgeScope] = mapped_column(
        enum_type(KnowledgeScope), default=KnowledgeScope.PLATFORM_SHARED, nullable=False
    )
    jurisdiction: Mapped[Optional[str]] = mapped_column(String(120))
    language: Mapped[Optional[str]] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)
    canonical_url: Mapped[Optional[str]] = mapped_column(String(500))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    assessment_family: Mapped[AssessmentFamily] = mapped_column(
        enum_type(AssessmentFamily), nullable=False
    )
    provider_code: Mapped[Optional[str]] = mapped_column(String(80))
    exam_family: Mapped[Optional[str]] = mapped_column(String(120))
    cefr_level: Mapped[Optional[str]] = mapped_column(String(10))
    skill: Mapped[Optional[str]] = mapped_column(String(80))

    authority: Mapped[KnowledgeAuthority] = relationship(back_populates="sources")
    versions: Mapped[list[KnowledgeDocumentVersion]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class KnowledgeDocumentVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "version_label", name="uq_knowledge_version_label"),
        Index("ix_knowledge_versions_source_status", "source_id", "status"),
        Index("ix_knowledge_versions_checksum", "sha256"),
        CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_knowledge_version_effective_dates",
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(120), nullable=False)
    publication_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    effective_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[KnowledgeVersionStatus] = mapped_column(
        enum_type(KnowledgeVersionStatus), default=KnowledgeVersionStatus.DRAFT, nullable=False
    )
    original_filename: Mapped[Optional[str]] = mapped_column(String(255))
    mime_type: Mapped[Optional[str]] = mapped_column(String(120))
    storage_key: Mapped[Optional[str]] = mapped_column(String(500))
    file_size: Mapped[Optional[int]] = mapped_column(Integer)
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    source_url: Mapped[Optional[str]] = mapped_column(String(500))
    retrieved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(500))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    supersedes_document_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="SET NULL")
    )

    source: Mapped[KnowledgeSource] = relationship(back_populates="versions")
    pages: Mapped[list[KnowledgeDocumentPage]] = relationship(
        back_populates="document_version", cascade="all, delete-orphan"
    )
    legal_metadata: Mapped[Optional[KnowledgeLegalMetadata]] = relationship(
        back_populates="document_version", uselist=False, cascade="all, delete-orphan"
    )
    ingestion_jobs: Mapped[list[KnowledgeIngestionJob]] = relationship(
        back_populates="document_version", cascade="all, delete-orphan"
    )


class KnowledgeLegalMetadata(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_legal_metadata"
    __table_args__ = (UniqueConstraint("document_version_id", name="uq_knowledge_legal_version"),)

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"), nullable=False
    )
    copyright_status: Mapped[str] = mapped_column(String(40), default="UNKNOWN", nullable=False)
    licence_type: Mapped[Optional[str]] = mapped_column(String(120))
    rights_holder: Mapped[Optional[str]] = mapped_column(String(300))
    usage_rights: Mapped[Optional[str]] = mapped_column(Text)
    redistribution_allowed: Mapped[Optional[bool]] = mapped_column(Boolean)
    derivative_use_allowed: Mapped[Optional[bool]] = mapped_column(Boolean)
    ai_processing_allowed: Mapped[Optional[bool]] = mapped_column(Boolean)
    citation_required: Mapped[Optional[bool]] = mapped_column(Boolean)
    commercial_use_allowed: Mapped[Optional[bool]] = mapped_column(Boolean)
    legal_notes: Mapped[Optional[str]] = mapped_column(Text)
    legal_review_status: Mapped[LegalReviewStatus] = mapped_column(
        enum_type(LegalReviewStatus), default=LegalReviewStatus.NOT_REVIEWED, nullable=False
    )
    legal_reviewed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    legal_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    document_version: Mapped[KnowledgeDocumentVersion] = relationship(
        back_populates="legal_metadata"
    )


class KnowledgeIngestionJob(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_ingestion_jobs"
    __table_args__ = (Index("ix_knowledge_jobs_version_status", "document_version_id", "status"),)

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[KnowledgeIngestionStatus] = mapped_column(
        enum_type(KnowledgeIngestionStatus),
        default=KnowledgeIngestionStatus.PENDING,
        nullable=False,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    parser_name: Mapped[Optional[str]] = mapped_column(String(120))
    parser_version: Mapped[Optional[str]] = mapped_column(String(80))
    pages_detected: Mapped[Optional[int]] = mapped_column(Integer)
    error_code: Mapped[Optional[str]] = mapped_column(String(80))
    error_message_safe: Mapped[Optional[str]] = mapped_column(String(500))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    document_version: Mapped[KnowledgeDocumentVersion] = relationship(
        back_populates="ingestion_jobs"
    )


class KnowledgeDocumentPage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_document_pages"
    __table_args__ = (
        UniqueConstraint("document_version_id", "page_number", name="uq_knowledge_page_number"),
        Index("ix_knowledge_pages_version", "document_version_id"),
    )

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"), nullable=False
    )
    ingestion_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_ingestion_jobs.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        enum_type(ExtractionStatus), nullable=False
    )
    heading: Mapped[Optional[str]] = mapped_column(String(500))
    parser_name: Mapped[str] = mapped_column(String(120), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    document_version: Mapped[KnowledgeDocumentVersion] = relationship(back_populates="pages")
