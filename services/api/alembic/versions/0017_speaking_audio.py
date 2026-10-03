"""Add governed recorded speaking audio and transcription identities."""

import sqlalchemy as sa

from alembic import op

revision = "0017_speaking_audio"
down_revision = "0016_writing_assessment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_audio_recording_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("exam_version_item_binding_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_user_id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("recording_started_at", sa.DateTime(timezone=True)),
        sa.Column("recording_expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["candidate_exam_attempts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["exam_version_item_binding_id"], ["exam_version_item_bindings.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["candidate_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "attempt_id",
            "exam_version_item_binding_id",
            "attempt_number",
            name="uq_audio_recording_attempt",
        ),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_table(
        "candidate_media_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_user_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("exam_version_item_binding_id", sa.Uuid(), nullable=False),
        sa.Column("recording_session_id", sa.Uuid(), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(255)),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organisation_id"], ["organisations.id"]),
        sa.ForeignKeyConstraint(["candidate_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["attempt_id"], ["candidate_exam_attempts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["exam_version_item_binding_id"], ["exam_version_item_bindings.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recording_session_id"], ["candidate_audio_recording_sessions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recording_session_id"),
        sa.UniqueConstraint("sha256"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_table(
        "speech_transcriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(20), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["candidate_media_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "speech_transcription_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("transcription_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("candidate_media_asset_id", sa.Uuid(), nullable=False),
        sa.Column("audio_sha256", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.Uuid()),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("segments", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_version_id", sa.Uuid()),
        sa.ForeignKeyConstraint(
            ["transcription_id"], ["speech_transcriptions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_media_asset_id"], ["candidate_media_assets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["ai_model_profiles.id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_version_id"], ["speech_transcription_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transcription_id", "version_number", name="uq_speech_transcription_version"
        ),
    )


def downgrade() -> None:
    op.drop_table("speech_transcription_versions")
    op.drop_table("speech_transcriptions")
    op.drop_table("candidate_media_assets")
    op.drop_table("candidate_audio_recording_sessions")
