"""Record honest human assessment origins and durable governance operations."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0025_writing_human_review"
down_revision: Union[str, None] = "0024_result_release"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Historical rows are deliberately not reclassified as AI or HUMAN.
    op.add_column(
        "subjective_assessment_versions",
        sa.Column("assessment_origin", sa.String(16), nullable=False, server_default="UNSPECIFIED"),
    )
    op.alter_column("subjective_criterion_assessments", "confidence", nullable=True)
    op.create_check_constraint(
        "ck_subjective_assessment_origin",
        "subjective_assessment_versions",
        "assessment_origin IN ('UNSPECIFIED', 'AI', 'HUMAN')",
    )
    op.create_table(
        "writing_governance_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id"), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("request_checksum", sa.String(64), nullable=False),
        sa.Column("result_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organisation_id", "operation_id", name="uq_writing_operation_scope"),
    )


def downgrade() -> None:
    # Human criteria intentionally have no AI confidence; restoring NOT NULL would
    # either discard real history or invent a confidence. Require an empty set.
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM subjective_criterion_assessments WHERE confidence IS NULL")
    ):
        raise RuntimeError("human assessment history prevents a lossless downgrade")
    op.drop_table("writing_governance_operations")
    op.drop_constraint(
        "ck_subjective_assessment_origin", "subjective_assessment_versions", type_="check"
    )
    op.alter_column("subjective_criterion_assessments", "confidence", nullable=False)
    op.drop_column("subjective_assessment_versions", "assessment_origin")
