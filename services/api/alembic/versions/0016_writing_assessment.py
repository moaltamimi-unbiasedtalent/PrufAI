"""Add governed writing rubrics and subjective assessment versions."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0016_writing_assessment"
down_revision: Union[str, None] = "0015_scoring_results"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    enum = lambda: sa.String(40)  # noqa: E731
    op.create_table(
        "assessment_rubrics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(160), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id")),
        sa.Column("status", enum(), nullable=False),
        sa.UniqueConstraint("code", "organisation_id", name="uq_assessment_rubric_scope_code"),
    )
    op.create_table(
        "rubric_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "rubric_id",
            sa.Uuid(),
            sa.ForeignKey("assessment_rubrics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("assessment_family", sa.String(40), nullable=False),
        sa.Column("language", sa.String(20)),
        sa.Column("cefr_levels", sa.JSON(), nullable=False),
        sa.Column("skill", sa.String(40), nullable=False),
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
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("performance_levels", sa.JSON(), nullable=False),
        sa.Column("aggregation_policy", sa.JSON(), nullable=False),
        sa.Column("evidence_policy", sa.JSON(), nullable=False),
        sa.Column("ai_assessment_policy", sa.JSON(), nullable=False),
        sa.Column("human_review_policy", sa.JSON(), nullable=False),
        sa.Column("status", enum(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column(
            "supersedes_version_id",
            sa.Uuid(),
            sa.ForeignKey("rubric_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("published_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("rubric_id", "version_label", name="uq_rubric_version_label"),
    )
    op.create_index("ix_rubric_versions_status", "rubric_versions", ["status"])
    op.create_table(
        "rubric_evidence_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "rubric_version_id",
            sa.Uuid(),
            sa.ForeignKey("rubric_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_document_version_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_page_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_document_pages.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "knowledge_chunk_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_chunks.id", ondelete="RESTRICT"),
        ),
        sa.Column("reference", sa.JSON(), nullable=False),
        sa.Column("purpose", sa.String(120), nullable=False),
        sa.Column("source_checksum", sa.String(64)),
        sa.Column("required", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_rubric_evidence_version", "rubric_evidence_bindings", ["rubric_version_id"])
    op.create_table(
        "subjective_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_exam_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "exam_version_id",
            sa.Uuid(),
            sa.ForeignKey("exam_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "exam_version_item_binding_id",
            sa.Uuid(),
            sa.ForeignKey("exam_version_item_bindings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "question_bank_item_version_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_item_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("candidate_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "attempt_id", "exam_version_item_binding_id", name="uq_subjective_assessment_item"
        ),
    )
    op.create_index("ix_subjective_assessment_org", "subjective_assessments", ["organisation_id"])
    op.create_table(
        "subjective_assessment_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "subjective_assessment_id",
            sa.Uuid(),
            sa.ForeignKey("subjective_assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "attempt_id", sa.Uuid(), sa.ForeignKey("candidate_exam_attempts.id"), nullable=False
        ),
        sa.Column("exam_version_id", sa.Uuid(), sa.ForeignKey("exam_versions.id"), nullable=False),
        sa.Column(
            "exam_version_item_binding_id",
            sa.Uuid(),
            sa.ForeignKey("exam_version_item_bindings.id"),
            nullable=False,
        ),
        sa.Column(
            "question_bank_item_version_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_item_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_response_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_responses.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_response_revision_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_response_revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "rubric_version_id", sa.Uuid(), sa.ForeignKey("rubric_versions.id"), nullable=False
        ),
        sa.Column(
            "standard_pack_version_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_versions.id"),
            nullable=False,
        ),
        sa.Column("status", enum(), nullable=False),
        sa.Column("response_checksum", sa.String(64), nullable=False),
        sa.Column("rubric_checksum", sa.String(64), nullable=False),
        sa.Column("workflow_version_id", sa.Uuid(), sa.ForeignKey("ai_workflow_versions.id")),
        sa.Column("agent_version_id", sa.Uuid(), sa.ForeignKey("agent_versions.id")),
        sa.Column("prompt_version_id", sa.Uuid(), sa.ForeignKey("prompt_versions.id")),
        sa.Column("model_profile_id", sa.Uuid(), sa.ForeignKey("ai_model_profiles.id")),
        sa.Column("ai_run_id", sa.Uuid(), sa.ForeignKey("ai_runs.id", ondelete="SET NULL")),
        sa.Column("proposed_score", sa.Numeric(12, 4)),
        sa.Column("approved_score", sa.Numeric(12, 4)),
        sa.Column("confidence_summary", sa.JSON(), nullable=False),
        sa.Column("disagreement_summary", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "supersedes_version_id",
            sa.Uuid(),
            sa.ForeignKey("subjective_assessment_versions.id", ondelete="SET NULL"),
        ),
        sa.UniqueConstraint(
            "subjective_assessment_id", "version_number", name="uq_subjective_assessment_version"
        ),
    )
    op.create_index(
        "ix_subjective_assessment_version_status", "subjective_assessment_versions", ["status"]
    )
    op.create_table(
        "subjective_criterion_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "subjective_assessment_version_id",
            sa.Uuid(),
            sa.ForeignKey("subjective_assessment_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("criterion_code", sa.String(120), nullable=False),
        sa.Column("proposed_level", sa.String(80), nullable=False),
        sa.Column("proposed_points", sa.Numeric(12, 4), nullable=False),
        sa.Column("approved_level", sa.String(80)),
        sa.Column("approved_points", sa.Numeric(12, 4)),
        sa.Column("confidence", sa.String(40), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("uncertainties", sa.JSON(), nullable=False),
        sa.Column("disagreement", sa.JSON(), nullable=False),
        sa.Column("ai_run_step_id", sa.Uuid(), sa.ForeignKey("ai_run_steps.id")),
        sa.Column("status", sa.String(40), nullable=False),
        sa.UniqueConstraint(
            "subjective_assessment_version_id",
            "criterion_code",
            name="uq_subjective_criterion_version",
        ),
    )


def downgrade() -> None:
    op.drop_table("subjective_criterion_assessments")
    op.drop_index(
        "ix_subjective_assessment_version_status", table_name="subjective_assessment_versions"
    )
    op.drop_table("subjective_assessment_versions")
    op.drop_index("ix_subjective_assessment_org", table_name="subjective_assessments")
    op.drop_table("subjective_assessments")
    op.drop_index("ix_rubric_evidence_version", table_name="rubric_evidence_bindings")
    op.drop_table("rubric_evidence_bindings")
    op.drop_index("ix_rubric_versions_status", table_name="rubric_versions")
    op.drop_table("rubric_versions")
    op.drop_table("assessment_rubrics")
