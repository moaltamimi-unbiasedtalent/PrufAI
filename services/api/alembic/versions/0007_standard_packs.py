"""Add governed Standard Pack identities, versions, bindings, and blueprints."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0007_standard_packs"
down_revision: Union[str, None] = "0006_retrieval_evaluation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "standard_packs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("assessment_family", sa.String(length=40), nullable=False),
        sa.Column("authority_id", sa.Uuid(), nullable=True),
        sa.Column("exam_family", sa.String(length=120), nullable=True),
        sa.Column("language", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["authority_id"], ["knowledge_authorities.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_standard_pack_code"),
    )
    op.create_index(
        "ix_standard_packs_family_status", "standard_packs", ["assessment_family", "status"]
    )
    op.create_table(
        "standard_pack_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("standard_pack_id", sa.Uuid(), nullable=False),
        sa.Column("version_label", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_version_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("configuration_schema_version", sa.String(length=40), nullable=False),
        sa.Column("configuration_checksum", sa.String(length=64), nullable=True),
        sa.Column("scoring_configuration_ref", sa.String(length=240), nullable=True),
        sa.Column("result_configuration_ref", sa.String(length=240), nullable=True),
        sa.Column("rubric_set_ref", sa.String(length=240), nullable=True),
        sa.Column("supported_levels", sa.JSON(), nullable=False),
        sa.Column("allowed_classifications", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["standard_pack_id"], ["standard_packs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_version_id"], ["standard_pack_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "standard_pack_id", "version_label", name="uq_standard_pack_version_label"
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_standard_pack_version_effective_dates",
        ),
    )
    op.create_index(
        "ix_standard_pack_versions_status_dates",
        "standard_pack_versions",
        ["status", "effective_from", "effective_until"],
    )
    op.create_table(
        "standard_pack_source_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("standard_pack_version_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_source_id", sa.Uuid(), nullable=True),
        sa.Column("document_version_id", sa.Uuid(), nullable=True),
        sa.Column("document_checksum", sa.String(length=64), nullable=True),
        sa.Column("binding_type", sa.String(length=30), nullable=False),
        sa.Column("purpose", sa.String(length=500), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["standard_pack_version_id"], ["standard_pack_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_source_id"], ["knowledge_sources.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"], ["knowledge_document_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "knowledge_source_id IS NOT NULL OR document_version_id IS NOT NULL",
            name="ck_standard_pack_binding_reference",
        ),
    )
    op.create_index(
        "ix_standard_pack_bindings_version",
        "standard_pack_source_bindings",
        ["standard_pack_version_id"],
    )
    op.create_table(
        "standard_pack_modules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("standard_pack_version_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("skill", sa.String(length=80), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("time_limit_seconds", sa.Integer(), nullable=True),
        sa.Column("preparation_time_seconds", sa.Integer(), nullable=True),
        sa.Column("navigation_mode", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["standard_pack_version_id"], ["standard_pack_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "standard_pack_version_id", "code", name="uq_standard_pack_module_code"
        ),
        sa.UniqueConstraint(
            "standard_pack_version_id", "sequence", name="uq_standard_pack_module_sequence"
        ),
        sa.CheckConstraint(
            "time_limit_seconds IS NULL OR time_limit_seconds >= 0", name="ck_pack_module_time"
        ),
    )
    op.create_table(
        "standard_pack_sections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("module_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("instructions_ref", sa.String(length=240), nullable=True),
        sa.Column("time_limit_seconds", sa.Integer(), nullable=True),
        sa.Column("navigation_mode", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["module_id"], ["standard_pack_modules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("module_id", "code", name="uq_standard_pack_section_code"),
        sa.UniqueConstraint("module_id", "sequence", name="uq_standard_pack_section_sequence"),
        sa.CheckConstraint(
            "time_limit_seconds IS NULL OR time_limit_seconds >= 0", name="ck_pack_section_time"
        ),
    )
    op.create_table(
        "standard_pack_task_specs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=60), nullable=False),
        sa.Column("min_items", sa.Integer(), nullable=False),
        sa.Column("max_items", sa.Integer(), nullable=False),
        sa.Column("required_items", sa.Integer(), nullable=True),
        sa.Column("points_per_item", sa.Integer(), nullable=True),
        sa.Column("response_mode", sa.String(length=30), nullable=False),
        sa.Column("media_requirement", sa.String(length=30), nullable=False),
        sa.Column("generation_allowed", sa.Boolean(), nullable=False),
        sa.Column("selection_allowed", sa.Boolean(), nullable=False),
        sa.Column("rubric_required", sa.Boolean(), nullable=False),
        sa.Column("rubric_reference", sa.String(length=240), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["section_id"], ["standard_pack_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("section_id", "code", name="uq_standard_pack_task_code"),
        sa.UniqueConstraint("section_id", "sequence", name="uq_standard_pack_task_sequence"),
        sa.CheckConstraint(
            "min_items >= 0 AND max_items >= min_items", name="ck_pack_task_item_range"
        ),
    )


def downgrade() -> None:
    op.drop_table("standard_pack_task_specs")
    op.drop_table("standard_pack_sections")
    op.drop_table("standard_pack_modules")
    op.drop_index("ix_standard_pack_bindings_version", table_name="standard_pack_source_bindings")
    op.drop_table("standard_pack_source_bindings")
    op.drop_index("ix_standard_pack_versions_status_dates", table_name="standard_pack_versions")
    op.drop_table("standard_pack_versions")
    op.drop_index("ix_standard_packs_family_status", table_name="standard_packs")
    op.drop_table("standard_packs")
