"""Add immutable governed objective correction bindings."""

import sqlalchemy as sa

from alembic import op

revision = "0023_objective_correction"
down_revision = "0022_result_correction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "result_correction_objective_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "result_correction_id",
            sa.Uuid(),
            sa.ForeignKey("result_corrections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "original_item_version_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_item_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "replacement_item_version_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_item_versions.id"),
            nullable=False,
        ),
        sa.Column("original_item_checksum", sa.String(64), nullable=False),
        sa.Column("replacement_item_checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "result_correction_id",
            "original_item_version_id",
            name="uq_result_correction_objective_source",
        ),
    )
    op.create_index(
        "ix_result_correction_objective_correction",
        "result_correction_objective_bindings",
        ["result_correction_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_result_correction_objective_correction",
        table_name="result_correction_objective_bindings",
    )
    op.drop_table("result_correction_objective_bindings")
