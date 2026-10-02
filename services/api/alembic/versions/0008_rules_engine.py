"""Add versioned deterministic Rules-as-Code governance and bindings."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0008_rules_engine"
down_revision: Union[str, None] = "0007_standard_packs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rule_definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "rule_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_definition_id", sa.Uuid(), nullable=False),
        sa.Column("version_label", sa.String(length=120), nullable=False),
        sa.Column("expression_schema_version", sa.String(length=40), nullable=False),
        sa.Column("expression", sa.JSON(), nullable=False),
        sa.Column("severity", sa.String(length=30), nullable=False),
        sa.Column("failure_code", sa.String(length=120), nullable=False),
        sa.Column("failure_message_template", sa.String(length=500), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_rule_version_id", sa.Uuid(), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("internal_policy", sa.Boolean(), nullable=False),
        sa.Column("test_cases", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule_definition_id"], ["rule_definitions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_rule_version_id"], ["rule_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_definition_id", "version_label", name="uq_rule_version_label"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_rule_version_effective_dates",
        ),
    )
    op.create_index(
        "ix_rule_versions_status_dates",
        "rule_versions",
        ["status", "effective_from", "effective_until"],
    )
    op.create_table(
        "rule_evidence_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_version_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_document_version_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_page_id", sa.Uuid(), nullable=True),
        sa.Column("knowledge_chunk_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_type", sa.String(length=30), nullable=False),
        sa.Column("document_checksum", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["rule_version_id"], ["rule_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["knowledge_document_version_id"],
            ["knowledge_document_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_page_id"], ["knowledge_document_pages.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_chunk_id"], ["knowledge_chunks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "knowledge_document_version_id IS NOT NULL", name="ck_rule_evidence_document_required"
        ),
    )
    op.create_index("ix_rule_evidence_rule_version", "rule_evidence_bindings", ["rule_version_id"])
    op.create_table(
        "standard_pack_rule_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("standard_pack_version_id", sa.Uuid(), nullable=False),
        sa.Column("rule_version_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=80), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["standard_pack_version_id"], ["standard_pack_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["rule_version_id"], ["rule_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "standard_pack_version_id", "rule_version_id", name="uq_pack_rule_version_binding"
        ),
    )
    op.create_index(
        "ix_pack_rule_bindings_pack", "standard_pack_rule_bindings", ["standard_pack_version_id"]
    )
    op.create_table(
        "rule_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )


def downgrade() -> None:
    op.drop_table("rule_sets")
    op.drop_index("ix_pack_rule_bindings_pack", table_name="standard_pack_rule_bindings")
    op.drop_table("standard_pack_rule_bindings")
    op.drop_index("ix_rule_evidence_rule_version", table_name="rule_evidence_bindings")
    op.drop_table("rule_evidence_bindings")
    op.drop_index("ix_rule_versions_status_dates", table_name="rule_versions")
    op.drop_table("rule_versions")
    op.drop_table("rule_definitions")
