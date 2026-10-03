"""Add deterministic scoring runs and governed result versions."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0015_scoring_results"
down_revision: Union[str, None] = "0014_candidate_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    enum = lambda: sa.String(40)  # noqa: E731
    op.create_table(
        "scoring_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_exam_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "exam_version_id",
            sa.Uuid(),
            sa.ForeignKey("exam_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "organisation_id",
            sa.Uuid(),
            sa.ForeignKey("organisations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "standard_pack_version_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", enum(), nullable=False),
        sa.Column("response_set_checksum", sa.String(64), nullable=False),
        sa.Column("scoring_engine_name", sa.String(120), nullable=False),
        sa.Column("scoring_engine_version", sa.String(40), nullable=False),
        sa.Column("configuration_checksum", sa.String(64), nullable=False),
        sa.Column("checksum", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("failure_category", sa.String(120)),
        sa.Column("initiated_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.UniqueConstraint(
            "attempt_id",
            "response_set_checksum",
            "scoring_engine_version",
            "configuration_checksum",
            name="uq_scoring_run_idempotency",
        ),
    )
    op.create_index("ix_scoring_runs_status", "scoring_runs", ["status"])
    op.create_table(
        "scoring_response_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scoring_run_id",
            sa.Uuid(),
            sa.ForeignKey("scoring_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_exam_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "exam_version_id",
            sa.Uuid(),
            sa.ForeignKey("exam_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("runtime_plan_checksum", sa.String(64), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_set_checksum", sa.String(64), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scoring_run_id", name="uq_scoring_snapshot_run"),
    )
    op.create_table(
        "item_scores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scoring_run_id",
            sa.Uuid(),
            sa.ForeignKey("scoring_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "exam_version_item_binding_id",
            sa.Uuid(),
            sa.ForeignKey("exam_version_item_bindings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "question_bank_item_version_id",
            sa.Uuid(),
            sa.ForeignKey("question_bank_item_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_response_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_responses.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "candidate_response_revision_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_response_revisions.id", ondelete="SET NULL"),
        ),
        sa.Column("scoring_policy", sa.String(80), nullable=False),
        sa.Column("status", enum(), nullable=False),
        sa.Column("awarded_points", sa.Numeric(12, 4)),
        sa.Column("maximum_points", sa.Numeric(12, 4), nullable=False),
        sa.Column("response_checksum", sa.String(64)),
        sa.Column("scoring_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "scoring_run_id", "exam_version_item_binding_id", name="uq_item_score_run_binding"
        ),
    )
    op.create_index("ix_item_scores_status", "item_scores", ["status"])
    op.create_table(
        "scoring_components",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scoring_run_id",
            sa.Uuid(),
            sa.ForeignKey("scoring_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("source_entity_id", sa.String(120), nullable=False),
        sa.Column("raw_awarded_points", sa.Numeric(12, 4)),
        sa.Column("raw_maximum_points", sa.Numeric(12, 4), nullable=False),
        sa.Column("transformed_value", sa.Numeric(12, 4)),
        sa.Column("status", enum(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scoring_components_level", "scoring_components", ["level"])
    op.create_table(
        "assessment_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_exam_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "organisation_id",
            sa.Uuid(),
            sa.ForeignKey("organisations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "exam_version_id",
            sa.Uuid(),
            sa.ForeignKey("exam_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("attempt_id", name="uq_result_attempt"),
    )
    op.create_table(
        "result_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "result_id",
            sa.Uuid(),
            sa.ForeignKey("assessment_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scoring_run_id",
            sa.Uuid(),
            sa.ForeignKey("scoring_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", enum(), nullable=False),
        sa.Column("response_set_checksum", sa.String(64), nullable=False),
        sa.Column("scoring_run_checksum", sa.String(64), nullable=False),
        sa.Column("raw_awarded_points", sa.Numeric(12, 4)),
        sa.Column("raw_maximum_points", sa.Numeric(12, 4)),
        sa.Column("outcome", enum()),
        sa.Column("incomplete_reason", sa.String(240)),
        sa.Column("rule_provenance", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_requested_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("released_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column(
            "supersedes_version_id",
            sa.Uuid(),
            sa.ForeignKey("result_versions.id", ondelete="SET NULL"),
        ),
        sa.UniqueConstraint("result_id", "version_number", name="uq_result_version_number"),
    )
    op.create_index("ix_result_versions_status", "result_versions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_result_versions_status", table_name="result_versions")
    op.drop_table("result_versions")
    op.drop_table("assessment_results")
    op.drop_index("ix_scoring_components_level", table_name="scoring_components")
    op.drop_table("scoring_components")
    op.drop_index("ix_item_scores_status", table_name="item_scores")
    op.drop_table("item_scores")
    op.drop_table("scoring_response_snapshots")
    op.drop_index("ix_scoring_runs_status", table_name="scoring_runs")
    op.drop_table("scoring_runs")
