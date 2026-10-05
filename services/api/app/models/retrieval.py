from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import (  # type: ignore[import-untyped]  # pgvector lacks PEP 561 metadata.
    VECTOR,
)
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import KnowledgeIndexJobStatus, KnowledgeIndexStatus
from app.models.identity import enum_type


class KnowledgeIndexProfile(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_index_profiles"
    __table_args__ = (UniqueConstraint("profile_id", name="uq_knowledge_index_profile_id"),)

    profile_id: Mapped[str] = mapped_column(String(120), nullable=False)
    chunker_name: Mapped[str] = mapped_column(String(120), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(40), nullable=False)
    chunk_configuration: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    embedding_provider: Mapped[str] = mapped_column(String(80), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(160), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    similarity_metric: Mapped[str] = mapped_column(String(30), nullable=False, default="cosine")
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "document_page_id",
            "chunk_index",
            "chunker_name",
            "chunker_version",
            name="uq_knowledge_chunk_identity",
        ),
        Index("ix_knowledge_chunks_version", "document_version_id"),
        Index("ix_knowledge_chunks_page", "document_page_id"),
        Index("ix_knowledge_chunks_hash", "content_hash"),
    )

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"), nullable=False
    )
    document_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_document_pages.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    character_start: Mapped[Optional[int]] = mapped_column(Integer)
    character_end: Mapped[Optional[int]] = mapped_column(Integer)
    section_title: Mapped[Optional[str]] = mapped_column(String(500))
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    chunker_name: Mapped[str] = mapped_column(String(120), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeChunkEmbedding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "profile_id", name="uq_knowledge_chunk_embedding_profile"),
        Index("ix_knowledge_embeddings_profile", "profile_id"),
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_index_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    embedding_provider: Mapped[str] = mapped_column(String(80), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(160), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(384), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeIndexJob(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_index_jobs"
    __table_args__ = (
        Index("ix_knowledge_index_jobs_version_status", "document_version_id", "status"),
    )

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_index_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[KnowledgeIndexJobStatus] = mapped_column(
        enum_type(KnowledgeIndexJobStatus), nullable=False
    )
    index_status: Mapped[KnowledgeIndexStatus] = mapped_column(
        enum_type(KnowledgeIndexStatus), nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    chunks_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embeddings_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[Optional[str]] = mapped_column(String(80))
    safe_error_message: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))


class KnowledgeRerankingProfile(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_reranking_profiles"
    __table_args__ = (UniqueConstraint("profile_id", name="uq_knowledge_reranking_profile_id"),)

    profile_id: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    candidate_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    final_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
