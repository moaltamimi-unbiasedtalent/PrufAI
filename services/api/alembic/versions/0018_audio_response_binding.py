"""Bind finalized candidate audio to an exact response revision."""

import sqlalchemy as sa

from alembic import op

revision = "0018_audio_response_binding"
down_revision = "0017_speaking_audio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_response_media_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_response_revision_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_media_asset_id", sa.Uuid(), nullable=False),
        sa.Column("binding_role", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_response_revision_id"],
            ["candidate_response_revisions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_media_asset_id"], ["candidate_media_assets.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_response_revision_id", "binding_role", name="uq_response_media_binding_role"
        ),
    )


def downgrade() -> None:
    op.drop_table("candidate_response_media_bindings")
