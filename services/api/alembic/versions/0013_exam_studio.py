"""Add governed Exam Studio composition tables."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0013_exam_studio"
down_revision: Union[str, None] = "0012_question_bank"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    enum = lambda: sa.String(60)  # noqa: E731
    op.create_table(
        "exams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(160), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.String(1000)),
        sa.Column("scope_type", sa.String(40), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("code", "organisation_id", name="uq_exam_scope_code"),
        sa.CheckConstraint(
            "(scope_type = 'PLATFORM_SHARED' AND organisation_id IS NULL) OR "
            "(scope_type = 'ORGANISATION_PRIVATE' AND organisation_id IS NOT NULL)",
            name="ck_exam_scope",
        ),
    )
    op.create_index("ix_exam_scope", "exams", ["scope_type", "organisation_id"])
    op.create_table(
        "exam_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "exam_id", sa.Uuid(), sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version_label", sa.String(80), nullable=False),
        sa.Column("status", enum(), nullable=False),
        sa.Column(
            "standard_pack_version_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("classification", enum(), nullable=False),
        sa.Column("origin_type", enum(), nullable=False),
        sa.Column("proposed_by_ai_run_id", sa.Uuid(), sa.ForeignKey("ai_runs.id")),
        sa.Column("proposed_by_ai_step_id", sa.Uuid(), sa.ForeignKey("ai_run_steps.id")),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column(
            "supersedes_version_id",
            sa.Uuid(),
            sa.ForeignKey("exam_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("timing_override", sa.JSON(), nullable=False),
        sa.Column("navigation_override", sa.JSON(), nullable=False),
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
        sa.UniqueConstraint("exam_id", "version_label", name="uq_exam_version_label"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_exam_version_effective_dates",
        ),
    )
    op.create_index(
        "ix_exam_version_resolution",
        "exam_versions",
        ["status", "effective_from", "effective_until"],
    )
    op.create_table(
        "exam_version_modules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "exam_version_id",
            sa.Uuid(),
            sa.ForeignKey("exam_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "standard_pack_module_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_modules.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "exam_version_id", "standard_pack_module_id", name="uq_exam_module_ref"
        ),
    )
    op.create_table(
        "exam_version_sections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "exam_version_module_id",
            sa.Uuid(),
            sa.ForeignKey("exam_version_modules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "standard_pack_section_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_sections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "exam_version_module_id", "standard_pack_section_id", name="uq_exam_section_ref"
        ),
    )
    op.create_table(
        "exam_version_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "exam_version_section_id",
            sa.Uuid(),
            sa.ForeignKey("exam_version_sections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "standard_pack_task_spec_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_task_specs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "exam_version_section_id", "standard_pack_task_spec_id", name="uq_exam_task_ref"
        ),
    )
    op.create_table(
        "exam_version_item_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "exam_version_task_id",
            sa.Uuid(),
            sa.ForeignKey("exam_version_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot_index", sa.Integer(), nullable=False),
        sa.Column(
            "question_bank_item_version_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_item_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("origin_type", enum(), nullable=False),
        sa.Column("proposed_by_ai_run_id", sa.Uuid(), sa.ForeignKey("ai_runs.id")),
        sa.Column("proposed_by_ai_step_id", sa.Uuid(), sa.ForeignKey("ai_run_steps.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exam_version_task_id", "slot_index", name="uq_exam_item_slot"),
        sa.CheckConstraint("slot_index >= 0", name="ck_exam_item_slot_nonnegative"),
    )


def downgrade() -> None:
    op.drop_table("exam_version_item_bindings")
    op.drop_table("exam_version_tasks")
    op.drop_table("exam_version_sections")
    op.drop_table("exam_version_modules")
    op.drop_index("ix_exam_version_resolution", table_name="exam_versions")
    op.drop_table("exam_versions")
    op.drop_index("ix_exam_scope", table_name="exams")
    op.drop_table("exams")
