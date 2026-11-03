"""Preserve independent audio identity and recoverable private upload stages."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0026_audio_sha_scope"
down_revision: Union[str, None] = "0025_writing_human_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for constraint in sa.inspect(op.get_bind()).get_unique_constraints("candidate_media_assets"):
        if constraint["column_names"] == ["sha256"]:
            op.drop_constraint(constraint["name"], "candidate_media_assets", type_="unique")
    op.create_index("ix_candidate_media_assets_sha256", "candidate_media_assets", ["sha256"])
    for name, kind in (
        ("storage_key", sa.String(500)),
        ("upload_sha256", sa.String(64)),
        ("upload_byte_size", sa.Integer()),
        ("upload_content_type", sa.String(120)),
        ("verified_duration_ms", sa.Integer()),
        ("last_error_category", sa.String(80)),
        ("upload_expires_at", sa.DateTime(timezone=True)),
        ("upload_started_at", sa.DateTime(timezone=True)),
        ("upload_completed_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("candidate_audio_recording_sessions", sa.Column(name, kind, nullable=True))
    for table, name in (
        ("candidate_audio_recording_sessions", "validation_provenance"),
        ("candidate_audio_recording_sessions", "evidence_identity"),
        ("candidate_media_assets", "validation_provenance"),
    ):
        op.add_column(
            table, sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        op.alter_column(table, name, server_default=None)
    op.execute(
        sa.text(
            "UPDATE candidate_audio_recording_sessions r SET storage_key = a.storage_key "
            "FROM candidate_media_assets a WHERE a.recording_session_id = r.id"
        )
    )
    op.create_unique_constraint(
        "uq_audio_recording_storage_key", "candidate_audio_recording_sessions", ["storage_key"]
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM (SELECT sha256 FROM candidate_media_assets "
            "GROUP BY sha256 HAVING count(*) > 1) d"
        )
    ):
        raise RuntimeError("independently owned identical audio prevents a lossless downgrade")
    op.drop_constraint(
        "uq_audio_recording_storage_key", "candidate_audio_recording_sessions", type_="unique"
    )
    for name in (
        "storage_key",
        "upload_sha256",
        "upload_byte_size",
        "upload_content_type",
        "verified_duration_ms",
        "last_error_category",
        "upload_expires_at",
        "upload_started_at",
        "upload_completed_at",
        "validation_provenance",
        "evidence_identity",
    ):
        op.drop_column("candidate_audio_recording_sessions", name)
    op.drop_column("candidate_media_assets", "validation_provenance")
    op.drop_index("ix_candidate_media_assets_sha256", table_name="candidate_media_assets")
    op.create_unique_constraint(
        "candidate_media_assets_sha256_key", "candidate_media_assets", ["sha256"]
    )
