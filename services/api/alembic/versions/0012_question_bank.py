"""Add governed question bank identities, versions and bindings."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0012_question_bank"
down_revision: Union[str, None] = "0011_governed_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    enum = lambda: sa.String(60)  # noqa: E731
    op.create_table(
        "question_bank_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(160), nullable=False),
        sa.Column("scope_type", sa.String(40), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id")),
        sa.Column("status", enum(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("code", "organisation_id", name="uq_question_bank_item_scope_code"),
        sa.CheckConstraint(
            "(scope_type = 'PLATFORM_SHARED' AND organisation_id IS NULL) OR "
            "(scope_type = 'ORGANISATION_PRIVATE' AND organisation_id IS NOT NULL)",
            name="ck_question_bank_item_scope",
        ),
    )
    op.create_index(
        "ix_question_bank_item_scope_status",
        "question_bank_items",
        ["scope_type", "organisation_id", "status"],
    )
    op.create_table(
        "question_bank_item_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(80), nullable=False),
        sa.Column("status", enum(), nullable=False),
        sa.Column("assessment_family", enum(), nullable=False),
        sa.Column("exam_family", sa.String(120)),
        sa.Column("language", sa.String(20)),
        sa.Column("skill", enum()),
        sa.Column("task_type", enum(), nullable=False),
        sa.Column("response_mode", enum(), nullable=False),
        sa.Column("media_requirement", enum(), nullable=False),
        sa.Column("difficulty_label", sa.String(40)),
        sa.Column("content_schema_version", sa.String(40), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("scoring_policy", enum(), nullable=False),
        sa.Column("max_points", sa.Numeric(12, 4)),
        sa.Column("scoring_metadata", sa.JSON(), nullable=False),
        sa.Column("accessibility", sa.JSON(), nullable=False),
        sa.Column("rights_status", enum(), nullable=False),
        sa.Column("rights_holder", sa.String(240)),
        sa.Column("licence_reference", sa.String(240)),
        sa.Column("permitted_use", sa.String(500)),
        sa.Column("rights_expires_at", sa.DateTime(timezone=True)),
        sa.Column("rights_notes", sa.Text()),
        sa.Column("rubric_reference", sa.String(240)),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column(
            "supersedes_version_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_item_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("origin_type", enum(), nullable=False),
        sa.Column("proposed_by_ai_run_id", sa.Uuid(), sa.ForeignKey("ai_runs.id")),
        sa.Column("proposed_by_ai_step_id", sa.Uuid(), sa.ForeignKey("ai_run_steps.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("published_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.UniqueConstraint("item_id", "version_label", name="uq_question_bank_item_version"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_question_bank_effective_dates",
        ),
        sa.CheckConstraint("max_points IS NULL OR max_points >= 0", name="ck_question_bank_points"),
    )
    op.create_index(
        "ix_question_bank_version_resolution",
        "question_bank_item_versions",
        ["status", "assessment_family", "language"],
    )
    op.create_table(
        "question_bank_item_cefr_levels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_version_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_item_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cefr_level", sa.String(2), nullable=False),
        sa.UniqueConstraint("item_version_id", "cefr_level", name="uq_question_bank_cefr"),
        sa.CheckConstraint(
            "cefr_level IN ('A1','A2','B1','B2','C1','C2')", name="ck_question_bank_cefr"
        ),
    )
    op.create_table(
        "question_bank_item_evidence_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_version_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_item_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_document_version_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_document_versions.id"),
        ),
        sa.Column("knowledge_page_id", sa.Uuid(), sa.ForeignKey("knowledge_document_pages.id")),
        sa.Column("knowledge_chunk_id", sa.Uuid(), sa.ForeignKey("knowledge_chunks.id")),
        sa.Column("evidence_id", sa.String(240)),
        sa.Column("purpose", sa.String(80), nullable=False),
        sa.CheckConstraint(
            "knowledge_document_version_id IS NOT NULL OR knowledge_page_id IS NOT NULL "
            "OR knowledge_chunk_id IS NOT NULL OR evidence_id IS NOT NULL",
            name="ck_question_bank_evidence_reference",
        ),
    )
    op.create_table(
        "question_bank_standard_pack_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_version_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_item_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "standard_pack_version_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "task_spec_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_task_specs.id", ondelete="RESTRICT"),
        ),
        sa.Column("validation_status", sa.String(20), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "item_version_id",
            "standard_pack_version_id",
            "task_spec_id",
            name="uq_question_bank_pack_binding",
        ),
    )
    op.create_table(
        "question_bank_media_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "item_version_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_item_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_type", enum(), nullable=False),
        sa.Column("object_key", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("rights_reference", sa.String(240)),
        sa.Column("duration_seconds", sa.Numeric(12, 4)),
        sa.Column("dimensions", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("question_bank_media_assets")
    op.drop_table("question_bank_standard_pack_bindings")
    op.drop_table("question_bank_item_evidence_bindings")
    op.drop_table("question_bank_item_cefr_levels")
    op.drop_index("ix_question_bank_version_resolution", table_name="question_bank_item_versions")
    op.drop_table("question_bank_item_versions")
    op.drop_index("ix_question_bank_item_scope_status", table_name="question_bank_items")
    op.drop_table("question_bank_items")
