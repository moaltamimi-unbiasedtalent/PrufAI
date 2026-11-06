"""Pin metadata-only source references separately from document bytes."""

import sqlalchemy as sa

from alembic import op

revision = "0037_b1_foundation"
down_revision = "0036_source_rights"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "standard_pack_source_bindings", sa.Column("source_metadata_checksum", sa.String(64))
    )


def downgrade() -> None:
    op.drop_column("standard_pack_source_bindings", "source_metadata_checksum")
