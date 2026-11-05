"""Persist governed feedback, pattern versions and improvement proposals."""

import sqlalchemy as sa

from alembic import op

revision = "0033_governed_feedback"
down_revision = "0032_eval_regression"
branch_labels = None
depends_on = None

_TABLES = (
    "governed_feedback",
    "feedback_pattern_versions",
    "improvement_proposal_versions",
    "feedback_operations",
)


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _scope() -> sa.Column:
    return sa.Column(
        "organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id", ondelete="RESTRICT")
    )


def _creation() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "governed_feedback",
        _id(),
        _scope(),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), unique=True, nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("evidence_class", sa.String(48), nullable=False),
        sa.Column("privacy_class", sa.String(48), nullable=False),
        *_creation(),
        sa.CheckConstraint(
            "source_kind IN ('EVALUATION_FAILURE','REVIEWER_FLAG',"
            "'GOVERNED_APPEAL_OUTCOME','OPERATIONAL_FAILURE')",
            name="ck_feedback_source_kind",
        ),
    )
    op.create_index("ix_feedback_scope", "governed_feedback", ["organisation_id", "scope_key"])
    op.create_index("ix_feedback_created", "governed_feedback", ["organisation_id", "created_at"])
    op.create_table(
        "feedback_pattern_versions",
        _id(),
        _scope(),
        sa.Column("group_key", sa.String(64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        *_creation(),
        sa.UniqueConstraint("group_key", "version_number", name="uq_feedback_pattern_version"),
        sa.CheckConstraint("version_number > 0", name="ck_feedback_pattern_version"),
    )
    op.create_index(
        "ix_feedback_pattern_created",
        "feedback_pattern_versions",
        ["organisation_id", "created_at"],
    )
    op.create_table(
        "improvement_proposal_versions",
        _id(),
        _scope(),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "pattern_id",
            sa.Uuid(),
            sa.ForeignKey("feedback_pattern_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        *_creation(),
        sa.UniqueConstraint(
            "proposal_id", "version_number", name="uq_improvement_proposal_version"
        ),
        sa.CheckConstraint("version_number > 0", name="ck_improvement_proposal_version"),
    )
    op.create_index(
        "ix_improvement_proposal_created",
        "improvement_proposal_versions",
        ["organisation_id", "created_at"],
    )
    op.create_table(
        "feedback_operations",
        _id(),
        _scope(),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("entity_kind", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("request_checksum", sa.String(64), nullable=False),
        sa.Column("previous_checksum", sa.String(64)),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "entity_kind", "entity_id", "revision", name="uq_feedback_operation_state"
        ),
        sa.CheckConstraint(
            "entity_kind IN ('FEEDBACK','PATTERN','PROPOSAL')",
            name="ck_feedback_operation_kind",
        ),
        sa.CheckConstraint(
            "(revision = 1 AND previous_checksum IS NULL) OR "
            "(revision > 1 AND previous_checksum IS NOT NULL)",
            name="ck_feedback_operation_predecessor",
        ),
    )
    op.execute("""
    CREATE FUNCTION prufai_feedback_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'FEEDBACK_HISTORY_IMMUTABLE';
    END $$;
    """)
    for table in _TABLES:
        op.execute(
            f"CREATE TRIGGER feedback_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prufai_feedback_immutable()"
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION prufai_feedback_immutable()")
