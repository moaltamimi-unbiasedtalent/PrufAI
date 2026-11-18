"""Persist candidate-owned LiD writer generations across processes and restarts."""

import sqlalchemy as sa

from alembic import op

revision = "0045_lid_writer_fencing"
down_revision = "0044_v1_worker_exclusion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidate_exam_attempts", sa.Column("writer_client_hash", sa.String(64)))
    op.add_column(
        "candidate_exam_attempts",
        sa.Column("writer_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "candidate_exam_attempts", sa.Column("writer_claimed_at", sa.DateTime(timezone=True))
    )
    op.create_check_constraint(
        "ck_candidate_writer_generation", "candidate_exam_attempts", "writer_generation >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_candidate_writer_generation", "candidate_exam_attempts", type_="check")
    op.drop_column("candidate_exam_attempts", "writer_claimed_at")
    op.drop_column("candidate_exam_attempts", "writer_generation")
    op.drop_column("candidate_exam_attempts", "writer_client_hash")
