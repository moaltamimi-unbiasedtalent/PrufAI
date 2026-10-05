"""Add versioned result moderation and appeal governance."""

import sqlalchemy as sa

from alembic import op

revision = "0021_result_governance"
down_revision = "0020_speaking_assessment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    enum = lambda: sa.String(48)  # noqa: E731
    op.create_table(
        "result_appeals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("candidate_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "attempt_id", sa.Uuid(), sa.ForeignKey("candidate_exam_attempts.id"), nullable=False
        ),
        sa.Column("result_id", sa.Uuid(), sa.ForeignKey("assessment_results.id"), nullable=False),
        sa.Column(
            "challenged_result_version_id",
            sa.Uuid(),
            sa.ForeignKey("result_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scoring_run_id", sa.Uuid(), sa.ForeignKey("scoring_runs.id"), nullable=False),
        sa.Column("exam_version_id", sa.Uuid(), sa.ForeignKey("exam_versions.id"), nullable=False),
        sa.Column(
            "standard_pack_version_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_versions.id"),
            nullable=False,
        ),
        sa.Column("challenged_result_checksum", sa.String(64), nullable=False),
        sa.Column("status", enum(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("operation_id", sa.String(120), nullable=False),
        sa.Column("current_version_number", sa.Integer(), nullable=False),
        sa.Column("current_checksum", sa.String(64), nullable=False),
        sa.Column(
            "corrected_result_version_id",
            sa.Uuid(),
            sa.ForeignKey("result_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("operation_id", name="uq_result_appeal_operation"),
    )
    op.create_index(
        "ix_result_appeals_queue", "result_appeals", ["organisation_id", "status", "created_at"]
    )
    op.create_index(
        "ix_result_appeals_candidate", "result_appeals", ["candidate_user_id", "created_at"]
    )
    op.create_index(
        "ix_result_appeals_result_version", "result_appeals", ["challenged_result_version_id"]
    )
    op.create_table(
        "result_appeal_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "appeal_id",
            sa.Uuid(),
            sa.ForeignKey("result_appeals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", enum(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("grounds", sa.Text(), nullable=False),
        sa.Column("additional_information", sa.Text()),
        sa.Column(
            "challenged_result_version_id",
            sa.Uuid(),
            sa.ForeignKey("result_versions.id"),
            nullable=False,
        ),
        sa.Column("challenged_result_checksum", sa.String(64), nullable=False),
        sa.Column("evidence_bundle_checksum", sa.String(64), nullable=False),
        sa.Column("operation_id", sa.String(120), nullable=False),
        sa.Column("decision", enum()),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("correction_category", sa.String(80)),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("appeal_id", "version_number", name="uq_result_appeal_version_number"),
        sa.UniqueConstraint("operation_id", name="uq_result_appeal_version_operation"),
    )
    op.create_index("ix_result_appeal_versions_status", "result_appeal_versions", ["status"])
    op.create_table(
        "appeal_information_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "appeal_id",
            sa.Uuid(),
            sa.ForeignKey("result_appeals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "appeal_version_id",
            sa.Uuid(),
            sa.ForeignKey("result_appeal_versions.id"),
            nullable=False,
        ),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_appeal_information_appeal", "appeal_information_entries", ["appeal_id", "created_at"]
    )
    op.create_table(
        "result_moderation_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column(
            "attempt_id", sa.Uuid(), sa.ForeignKey("candidate_exam_attempts.id"), nullable=False
        ),
        sa.Column("result_id", sa.Uuid(), sa.ForeignKey("assessment_results.id"), nullable=False),
        sa.Column(
            "result_version_id", sa.Uuid(), sa.ForeignKey("result_versions.id"), nullable=False
        ),
        sa.Column("scoring_run_id", sa.Uuid(), sa.ForeignKey("scoring_runs.id"), nullable=False),
        sa.Column("result_checksum", sa.String(64), nullable=False),
        sa.Column("status", enum(), nullable=False),
        sa.Column("reason", sa.String(120), nullable=False),
        sa.Column("priority", sa.String(30), nullable=False),
        sa.Column("outcome", sa.String(40)),
        sa.Column("operation_id", sa.String(120), nullable=False),
        sa.Column("assigned_to_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.UniqueConstraint("operation_id", name="uq_result_moderation_operation"),
    )
    op.create_index(
        "ix_result_moderation_queue",
        "result_moderation_cases",
        ["organisation_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_result_moderation_queue", table_name="result_moderation_cases")
    op.drop_table("result_moderation_cases")
    op.drop_index("ix_appeal_information_appeal", table_name="appeal_information_entries")
    op.drop_table("appeal_information_entries")
    op.drop_index("ix_result_appeal_versions_status", table_name="result_appeal_versions")
    op.drop_table("result_appeal_versions")
    op.drop_index("ix_result_appeals_result_version", table_name="result_appeals")
    op.drop_index("ix_result_appeals_candidate", table_name="result_appeals")
    op.drop_index("ix_result_appeals_queue", table_name="result_appeals")
    op.drop_table("result_appeals")
