"""Persist the V1 worker exclusion on the PostgreSQL database."""

import sqlalchemy as sa

from alembic import op

revision = "0044_v1_worker_exclusion"
down_revision = "0043_privacy_case_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    policy = op.create_table(
        "v1_worker_policy",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("excluded", sa.Boolean(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_v1_worker_policy_singleton"),
        schema="public",
    )
    op.bulk_insert(policy, [{"id": 1, "excluded": False}])


def downgrade() -> None:
    op.drop_table("v1_worker_policy", schema="public")
