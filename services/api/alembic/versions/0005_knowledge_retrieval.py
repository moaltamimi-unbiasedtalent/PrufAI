"""Add chunked knowledge retrieval, embedding profiles, and index jobs."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR

from alembic import op

revision: str = "0005_knowledge_retrieval"
down_revision: Union[str, None] = "0004_standards_knowledge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_index_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.String(length=120), nullable=False),
        sa.Column("chunker_name", sa.String(length=120), nullable=False),
        sa.Column("chunker_version", sa.String(length=40), nullable=False),
        sa.Column("chunk_configuration", sa.Text(), nullable=False),
        sa.Column("embedding_provider", sa.String(length=80), nullable=False),
        sa.Column("embedding_model", sa.String(length=160), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("similarity_metric", sa.String(length=30), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", name="uq_knowledge_index_profile_id"),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("document_page_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("character_start", sa.Integer()),
        sa.Column("character_end", sa.Integer()),
        sa.Column("section_title", sa.String(length=500)),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("chunker_name", sa.String(length=120), nullable=False),
        sa.Column("chunker_version", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_page_id"], ["knowledge_document_pages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["knowledge_document_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id",
            "document_page_id",
            "chunk_index",
            "chunker_name",
            "chunker_version",
            name="uq_knowledge_chunk_identity",
        ),
    )
    op.create_index("ix_knowledge_chunks_version", "knowledge_chunks", ["document_version_id"])
    op.create_index("ix_knowledge_chunks_page", "knowledge_chunks", ["document_page_id"])
    op.create_index("ix_knowledge_chunks_hash", "knowledge_chunks", ["content_hash"])
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_fts ON knowledge_chunks "
        "USING gin (to_tsvector('simple', text_content))"
    )
    op.create_table(
        "knowledge_chunk_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_provider", sa.String(length=80), nullable=False),
        sa.Column("embedding_model", sa.String(length=160), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", VECTOR(384), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["knowledge_chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["knowledge_index_profiles.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id", "profile_id", name="uq_knowledge_chunk_embedding_profile"),
    )
    op.create_index("ix_knowledge_embeddings_profile", "knowledge_chunk_embeddings", ["profile_id"])
    op.create_table(
        "knowledge_index_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("index_status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("chunks_created", sa.Integer(), nullable=False),
        sa.Column("embeddings_created", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("safe_error_message", sa.String(length=500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid()),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["knowledge_document_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["knowledge_index_profiles.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_index_jobs_version_status",
        "knowledge_index_jobs",
        ["document_version_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_index_jobs_version_status", table_name="knowledge_index_jobs")
    op.drop_table("knowledge_index_jobs")
    op.drop_index("ix_knowledge_embeddings_profile", table_name="knowledge_chunk_embeddings")
    op.drop_table("knowledge_chunk_embeddings")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_fts")
    op.drop_index("ix_knowledge_chunks_hash", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_page", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_version", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_index_profiles")
