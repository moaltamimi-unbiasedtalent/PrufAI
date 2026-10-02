"""Add standards source registry, legal metadata, and ingestion provenance."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0004_standards_knowledge_foundation"
down_revision: Union[str, None] = "0003_authentication_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_authorities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("authority_type", sa.String(length=40), nullable=False),
        sa.Column("jurisdiction", sa.String(length=120), nullable=True),
        sa.Column("website_url", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "knowledge_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("authority_id", sa.Uuid(), nullable=False),
        sa.Column("source_code", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("document_type", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("jurisdiction", sa.String(length=120), nullable=True),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("canonical_url", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("assessment_family", sa.String(length=40), nullable=False),
        sa.Column("provider_code", sa.String(length=80), nullable=True),
        sa.Column("exam_family", sa.String(length=120), nullable=True),
        sa.Column("cefr_level", sa.String(length=10), nullable=True),
        sa.Column("skill", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["authority_id"], ["knowledge_authorities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_code", name="uq_knowledge_sources_code"),
        sa.CheckConstraint(
            "(assessment_family = 'LANGUAGE' AND "
            "(cefr_level IS NULL OR cefr_level IN "
            "('A1','A2','B1','B2','C1','C2'))) OR "
            "(assessment_family = 'INTEGRATION_KNOWLEDGE' AND cefr_level IS NULL)",
            name="ck_knowledge_source_taxonomy",
        ),
    )
    op.create_index("ix_knowledge_sources_authority", "knowledge_sources", ["authority_id"])
    op.create_index(
        "ix_knowledge_sources_taxonomy",
        "knowledge_sources",
        ["assessment_family", "exam_family", "cefr_level"],
    )
    op.create_table(
        "knowledge_document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version_label", sa.String(length=120), nullable=False),
        sa.Column("publication_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("supersedes_document_version_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_knowledge_version_effective_dates",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_document_version_id"],
            ["knowledge_document_versions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "version_label", name="uq_knowledge_version_label"),
    )
    op.create_index(
        "ix_knowledge_versions_source_status",
        "knowledge_document_versions",
        ["source_id", "status"],
    )
    op.create_index("ix_knowledge_versions_checksum", "knowledge_document_versions", ["sha256"])
    op.create_table(
        "knowledge_legal_metadata",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("copyright_status", sa.String(length=40), nullable=False),
        sa.Column("licence_type", sa.String(length=120), nullable=True),
        sa.Column("rights_holder", sa.String(length=300), nullable=True),
        sa.Column("usage_rights", sa.Text(), nullable=True),
        sa.Column("redistribution_allowed", sa.Boolean(), nullable=True),
        sa.Column("derivative_use_allowed", sa.Boolean(), nullable=True),
        sa.Column("ai_processing_allowed", sa.Boolean(), nullable=True),
        sa.Column("citation_required", sa.Boolean(), nullable=True),
        sa.Column("commercial_use_allowed", sa.Boolean(), nullable=True),
        sa.Column("legal_notes", sa.Text(), nullable=True),
        sa.Column("legal_review_status", sa.String(length=30), nullable=False),
        sa.Column("legal_reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("legal_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["knowledge_document_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["legal_reviewed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_version_id", name="uq_knowledge_legal_version"),
    )
    op.create_table(
        "knowledge_ingestion_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parser_name", sa.String(length=120), nullable=True),
        sa.Column("parser_version", sa.String(length=80), nullable=True),
        sa.Column("pages_detected", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message_safe", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["knowledge_document_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_jobs_version_status",
        "knowledge_ingestion_jobs",
        ["document_version_id", "status"],
    )
    op.create_table(
        "knowledge_document_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("ingestion_job_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("extraction_status", sa.String(length=20), nullable=False),
        sa.Column("heading", sa.String(length=500), nullable=True),
        sa.Column("parser_name", sa.String(length=120), nullable=False),
        sa.Column("parser_version", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["knowledge_document_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_job_id"], ["knowledge_ingestion_jobs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_version_id", "page_number", name="uq_knowledge_page_number"),
    )
    op.create_index(
        "ix_knowledge_pages_version", "knowledge_document_pages", ["document_version_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_pages_version", table_name="knowledge_document_pages")
    op.drop_table("knowledge_document_pages")
    op.drop_index("ix_knowledge_jobs_version_status", table_name="knowledge_ingestion_jobs")
    op.drop_table("knowledge_ingestion_jobs")
    op.drop_table("knowledge_legal_metadata")
    op.drop_index("ix_knowledge_versions_checksum", table_name="knowledge_document_versions")
    op.drop_index("ix_knowledge_versions_source_status", table_name="knowledge_document_versions")
    op.drop_table("knowledge_document_versions")
    op.drop_index("ix_knowledge_sources_taxonomy", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_authority", table_name="knowledge_sources")
    op.drop_table("knowledge_sources")
    op.drop_table("knowledge_authorities")
