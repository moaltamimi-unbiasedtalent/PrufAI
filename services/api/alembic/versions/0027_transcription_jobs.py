"""Durable, fenced execution of versioned speech transcription."""

from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "0027_transcription_jobs"
down_revision = "0026_audio_sha_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns: list[sa.Column[Any]] = [sa.Column("id", sa.Uuid(), primary_key=True)]
    references = {
        "transcription_id": "speech_transcriptions.id",
        "organisation_id": "organisations.id",
        "candidate_user_id": "users.id",
        "attempt_id": "candidate_exam_attempts.id",
        "exam_version_item_binding_id": "exam_version_item_bindings.id",
        "candidate_response_revision_id": "candidate_response_revisions.id",
        "candidate_response_media_binding_id": "candidate_response_media_bindings.id",
        "candidate_media_asset_id": "candidate_media_assets.id",
        "recording_session_id": "candidate_audio_recording_sessions.id",
        "requested_by_user_id": "users.id",
        "requested_membership_id": "organisation_memberships.id",
        "profile_id": "ai_model_profiles.id",
    }
    for name, target in references.items():
        columns.append(sa.Column(name, sa.Uuid(), sa.ForeignKey(target), nullable=False))
    columns.extend(
        [
            sa.Column("operation_id", sa.Uuid(), nullable=False, unique=True),
            sa.Column("audio_sha256", sa.String(64), nullable=False),
            sa.Column("byte_size", sa.Integer(), nullable=False),
            sa.Column("verified_duration_ms", sa.Integer(), nullable=False),
            sa.Column("source_identity", sa.JSON(), nullable=False),
            sa.Column("profile_version", sa.String(64), nullable=False),
            sa.Column("profile_snapshot", sa.JSON(), nullable=False),
            sa.Column("configuration_checksum", sa.String(64), nullable=False),
            sa.Column("snapshot_checksum", sa.String(64), nullable=False),
            sa.Column("language", sa.String(20), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("claim_token", sa.Uuid()),
            sa.Column("claimed_by", sa.String(120)),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
            sa.Column("failure_category", sa.String(80)),
            sa.Column("state_history", sa.JSON(), nullable=False),
        ]
    )
    op.create_table(
        "speech_transcription_jobs",
        *columns,
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','RETRYABLE','READY','FAILED','CANCELLED')",
            name="ck_transcription_job_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 3",
            name="ck_transcription_job_attempts",
        ),
    )
    op.create_index(
        "ix_transcription_job_claim",
        "speech_transcription_jobs",
        ["status", "next_attempt_at", "lease_expires_at"],
    )
    op.create_index(
        "uq_transcription_job_active",
        "speech_transcription_jobs",
        ["transcription_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED','RUNNING','RETRYABLE')"),
    )
    op.add_column("speech_transcription_versions", sa.Column("execution_job_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_transcription_version_job",
        "speech_transcription_versions",
        "speech_transcription_jobs",
        ["execution_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_transcription_version_job", "speech_transcription_versions", ["execution_job_id"]
    )
    op.add_column("speech_transcription_versions", sa.Column("profile_version", sa.String(64)))
    op.add_column(
        "speech_transcription_versions",
        sa.Column("provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("speech_transcription_versions", "provenance", server_default=None)


def downgrade() -> None:
    op.drop_column("speech_transcription_versions", "provenance")
    op.drop_column("speech_transcription_versions", "profile_version")
    op.drop_constraint(
        "uq_transcription_version_job", "speech_transcription_versions", type_="unique"
    )
    op.drop_constraint(
        "fk_transcription_version_job", "speech_transcription_versions", type_="foreignkey"
    )
    op.drop_column("speech_transcription_versions", "execution_job_id")
    op.drop_table("speech_transcription_jobs")
