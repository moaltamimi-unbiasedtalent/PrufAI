"""Add durable governed result-correction linkage."""

import sqlalchemy as sa

from alembic import op

revision = "0022_result_correction"
down_revision = "0021_result_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    enum = lambda: sa.String(48)  # noqa: E731
    op.create_table(
        "result_corrections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("appeal_id", sa.Uuid(), sa.ForeignKey("result_appeals.id")),
        sa.Column("appeal_version_id", sa.Uuid(), sa.ForeignKey("result_appeal_versions.id")),
        sa.Column("moderation_case_id", sa.Uuid(), sa.ForeignKey("result_moderation_cases.id")),
        sa.Column(
            "source_result_version_id",
            sa.Uuid(),
            sa.ForeignKey("result_versions.id"),
            nullable=False,
        ),
        sa.Column("source_result_checksum", sa.String(64), nullable=False),
        sa.Column("correction_type", enum(), nullable=False),
        sa.Column("status", enum(), nullable=False),
        sa.Column("operation_id", sa.String(120), nullable=False),
        sa.Column(
            "new_component_version_id",
            sa.Uuid(),
            sa.ForeignKey("subjective_assessment_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "new_transcription_version_id",
            sa.Uuid(),
            sa.ForeignKey("speech_transcription_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column("new_scoring_run_id", sa.Uuid(), sa.ForeignKey("scoring_runs.id")),
        sa.Column("new_result_version_id", sa.Uuid(), sa.ForeignKey("result_versions.id")),
        sa.Column("failure_category", sa.String(120)),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("operation_id", name="uq_result_correction_operation"),
    )
    op.create_index(
        "ix_result_corrections_queue",
        "result_corrections",
        ["organisation_id", "status", "created_at"],
    )
    op.create_index("ix_result_corrections_appeal", "result_corrections", ["appeal_id"])


def downgrade() -> None:
    op.drop_index("ix_result_corrections_appeal", table_name="result_corrections")
    op.drop_index("ix_result_corrections_queue", table_name="result_corrections")
    op.drop_table("result_corrections")
