"""Add durable, domain-scoped AI workflow checkpoints."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_ai_checkpoints"
down_revision: Union[str, None] = "0009_ai_orchestration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_workflow_checkpoints",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_version_id", sa.Uuid(), sa.ForeignKey("ai_workflow_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id")),
        sa.Column("node_id", sa.String(120), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_ai_checkpoint_run"),
    )


def downgrade() -> None:
    op.drop_table("ai_workflow_checkpoints")
