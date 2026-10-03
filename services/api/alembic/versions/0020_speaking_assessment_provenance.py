"""Add generic subjective provenance for governed speaking assessment."""

import sqlalchemy as sa

from alembic import op

revision = "0020_speaking_assessment"
down_revision = "0019_transcription_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subjective_assessment_versions",
        sa.Column("candidate_media_asset_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "subjective_assessment_versions", sa.Column("audio_sha256", sa.String(64), nullable=True)
    )
    op.add_column(
        "subjective_assessment_versions",
        sa.Column("speech_transcription_version_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "subjective_assessment_versions",
        sa.Column("transcript_checksum", sa.String(64), nullable=True),
    )
    for name in (
        "evidence_mode_summary",
        "ai_proposal",
        "human_approved_values",
        "consistency_summary",
    ):
        op.add_column(
            "subjective_assessment_versions",
            sa.Column(name, sa.JSON(), nullable=False, server_default="{}"),
        )
        op.alter_column("subjective_assessment_versions", name, server_default=None)
    op.create_foreign_key(
        "fk_subjective_version_media_asset",
        "subjective_assessment_versions",
        "candidate_media_assets",
        ["candidate_media_asset_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_subjective_version_transcript",
        "subjective_assessment_versions",
        "speech_transcription_versions",
        ["speech_transcription_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_subjective_version_transcript", "subjective_assessment_versions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_subjective_version_media_asset", "subjective_assessment_versions", type_="foreignkey"
    )
    for name in (
        "consistency_summary",
        "human_approved_values",
        "ai_proposal",
        "evidence_mode_summary",
        "transcript_checksum",
        "speech_transcription_version_id",
        "audio_sha256",
        "candidate_media_asset_id",
    ):
        op.drop_column("subjective_assessment_versions", name)
