"""Add governed long-term memory and run references."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0011_governed_memory"
down_revision: Union[str, None] = "0010_ai_checkpoints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    status = lambda: sa.String(40)  # noqa: E731
    op.create_table(
        "long_term_memories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(160), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("category", status(), nullable=False),
        sa.Column("scope_type", status(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id")),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", name="uq_long_term_memory_code"),
        sa.CheckConstraint(
            "(scope_type = 'PLATFORM_SHARED' AND organisation_id IS NULL) OR "
            "(scope_type = 'ORGANISATION_PRIVATE' AND organisation_id IS NOT NULL)",
            name="ck_memory_scope",
        ),
    )
    op.create_table(
        "long_term_memory_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "memory_id",
            sa.Uuid(),
            sa.ForeignKey("long_term_memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(80), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("content_schema_version", sa.String(40), nullable=False),
        sa.Column("status", status(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("review_after", sa.DateTime(timezone=True)),
        sa.Column(
            "supersedes_version_id", sa.Uuid(), sa.ForeignKey("long_term_memory_versions.id")
        ),
        sa.Column("origin_type", status(), nullable=False),
        sa.Column("proposed_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("proposed_by_ai_run_id", sa.Uuid(), sa.ForeignKey("ai_runs.id")),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.UniqueConstraint("memory_id", "version_label", name="uq_long_term_memory_version"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_memory_effective_window",
        ),
    )
    op.create_index(
        "ix_memory_version_resolution",
        "long_term_memory_versions",
        ["status", "effective_from", "expires_at"],
    )
    op.create_table(
        "memory_evidence_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "memory_version_id",
            sa.Uuid(),
            sa.ForeignKey("long_term_memory_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_document_version_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_document_versions.id"),
        ),
        sa.Column(
            "standard_pack_version_id", sa.Uuid(), sa.ForeignKey("standard_pack_versions.id")
        ),
        sa.Column("rule_version_id", sa.Uuid(), sa.ForeignKey("rule_versions.id")),
        sa.Column("ai_run_id", sa.Uuid(), sa.ForeignKey("ai_runs.id")),
        sa.Column("evidence_type", sa.String(80), nullable=False),
        sa.CheckConstraint(
            "knowledge_document_version_id IS NOT NULL OR standard_pack_version_id IS NOT NULL "
            "OR rule_version_id IS NOT NULL OR ai_run_id IS NOT NULL",
            name="ck_memory_evidence_reference",
        ),
    )
    op.create_table(
        "ai_run_memory_references",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "ai_run_id", sa.Uuid(), sa.ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "memory_version_id",
            sa.Uuid(),
            sa.ForeignKey("long_term_memory_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("context_rank", sa.Integer(), nullable=False),
        sa.UniqueConstraint("ai_run_id", "memory_version_id", name="uq_ai_run_memory_reference"),
    )


def downgrade() -> None:
    op.drop_table("ai_run_memory_references")
    op.drop_table("memory_evidence_bindings")
    op.drop_index("ix_memory_version_resolution", table_name="long_term_memory_versions")
    op.drop_table("long_term_memory_versions")
    op.drop_table("long_term_memories")
