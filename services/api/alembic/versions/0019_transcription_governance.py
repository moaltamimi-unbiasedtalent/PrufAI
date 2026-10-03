"""Add governed transcription lifecycle and provenance fields."""

import sqlalchemy as sa

from alembic import op

revision = "0019_transcription_governance"
down_revision = "0018_audio_response_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_speech_transcription_asset", "speech_transcriptions", ["asset_id"]
    )
    op.add_column("speech_transcriptions", sa.Column("operation_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        "uq_speech_transcription_operation", "speech_transcriptions", ["operation_id"]
    )
    op.add_column("speech_transcriptions", sa.Column("failure_category", sa.String(80)))
    op.add_column(
        "speech_transcriptions",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("speech_transcriptions", "retry_count", server_default=None)
    op.add_column(
        "speech_transcription_versions",
        sa.Column("status", sa.String(40), nullable=False, server_default="READY"),
    )
    op.add_column("speech_transcription_versions", sa.Column("provider", sa.String(120)))
    op.add_column("speech_transcription_versions", sa.Column("model_name", sa.String(160)))
    op.add_column("speech_transcription_versions", sa.Column("adapter_version", sa.String(80)))
    op.add_column("speech_transcription_versions", sa.Column("language", sa.String(20)))
    op.add_column(
        "speech_transcription_versions",
        sa.Column("configuration", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "speech_transcription_versions", sa.Column("configuration_checksum", sa.String(64))
    )
    op.add_column("speech_transcription_versions", sa.Column("failure_category", sa.String(80)))
    op.add_column(
        "speech_transcription_versions",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("speech_transcription_versions", "configuration", server_default=None)
    op.alter_column("speech_transcription_versions", "retry_count", server_default=None)
    op.alter_column("speech_transcription_versions", "status", server_default=None)


def downgrade() -> None:
    op.drop_column("speech_transcription_versions", "retry_count")
    op.drop_column("speech_transcription_versions", "failure_category")
    op.drop_column("speech_transcription_versions", "configuration_checksum")
    op.drop_column("speech_transcription_versions", "configuration")
    op.drop_column("speech_transcription_versions", "adapter_version")
    op.drop_column("speech_transcription_versions", "language")
    op.drop_column("speech_transcription_versions", "model_name")
    op.drop_column("speech_transcription_versions", "provider")
    op.drop_column("speech_transcription_versions", "status")
    op.drop_column("speech_transcriptions", "retry_count")
    op.drop_column("speech_transcriptions", "failure_category")
    op.drop_constraint("uq_speech_transcription_operation", "speech_transcriptions", type_="unique")
    op.drop_column("speech_transcriptions", "operation_id")
    op.drop_constraint("uq_speech_transcription_asset", "speech_transcriptions", type_="unique")
