"""Durable governed Writing proposal execution."""

import sqlalchemy as sa

from alembic import op

revision = "0028_writing_execution"
down_revision = "0027_transcription_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "writing_execution_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column(
            "organisation_id",
            sa.Uuid(),
            sa.ForeignKey("organisations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
        sa.Column(
            "candidate_response_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_responses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_response_revision_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_response_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "standard_pack_version_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "rubric_version_id",
            sa.Uuid(),
            sa.ForeignKey("rubric_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assessment_version_id",
            sa.Uuid(),
            sa.ForeignKey("subjective_assessment_versions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "ai_run_id",
            sa.Uuid(),
            sa.ForeignKey("ai_runs.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "requested_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "requested_membership_id",
            sa.Uuid(),
            sa.ForeignKey("organisation_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "execution_profile_id",
            sa.Uuid(),
            sa.ForeignKey("ai_workflow_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "active_step_id", sa.Uuid(), sa.ForeignKey("ai_run_steps.id", ondelete="RESTRICT")
        ),
        sa.Column("response_checksum", sa.String(64), nullable=False),
        sa.Column("standard_pack_checksum", sa.String(64), nullable=False),
        sa.Column("rubric_checksum", sa.String(64), nullable=False),
        sa.Column("profile_version", sa.String(64), nullable=False),
        sa.Column("snapshot_checksum", sa.String(64), nullable=False),
        sa.Column("input_checksum", sa.String(64), nullable=False),
        sa.Column("output_checksum", sa.String(64)),
        sa.Column("provider_request_id", sa.String(160)),
        sa.Column("source_identity", sa.JSON(), nullable=False),
        sa.Column("execution_snapshot", sa.JSON(), nullable=False),
        sa.Column("validated_proposal", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("claimed_by", sa.String(120)),
        sa.Column("failure_category", sa.String(80)),
        sa.Column("state_history", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','RETRYABLE','COMPLETED','FAILED','CANCELLED')",
            name="ck_writing_execution_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts AND max_attempts BETWEEN 1 AND 3",
            name="ck_writing_execution_attempts",
        ),
    )
    op.create_index(
        "ix_writing_execution_claim",
        "writing_execution_jobs",
        ["status", "next_attempt_at", "lease_expires_at"],
    )
    op.create_index(
        "uq_writing_execution_active",
        "writing_execution_jobs",
        ["attempt_id", "exam_version_item_binding_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED','RUNNING','RETRYABLE')"),
    )


def downgrade() -> None:
    op.drop_table("writing_execution_jobs")
