"""Link approved proposals to immutable prompt draft successors."""

import sqlalchemy as sa

from alembic import op

revision = "0034_improvement_candidates"
down_revision = "0033_governed_feedback"
branch_labels = None
depends_on = None

_TABLE = "governed_change_candidates"
_OLD_KINDS = "entity_kind IN ('FEEDBACK','PATTERN','PROPOSAL')"
_NEW_KINDS = "entity_kind IN ('FEEDBACK','PATTERN','PROPOSAL','CHANGE_CANDIDATE')"


def _replace_kinds(expression: str) -> None:
    op.drop_constraint("ck_feedback_operation_kind", "feedback_operations", type_="check")
    op.create_check_constraint("ck_feedback_operation_kind", "feedback_operations", expression)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organisation_id", sa.Uuid(), sa.ForeignKey("organisations.id", ondelete="RESTRICT")
        ),
        sa.Column(
            "proposal_version_id",
            sa.Uuid(),
            sa.ForeignKey("improvement_proposal_versions.id", ondelete="RESTRICT"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "baseline_prompt_version_id",
            sa.Uuid(),
            sa.ForeignKey("prompt_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "draft_prompt_version_id",
            sa.Uuid(),
            sa.ForeignKey("prompt_versions.id", ondelete="RESTRICT"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "suite_version_id",
            sa.Uuid(),
            sa.ForeignKey("evaluation_dataset_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("organisation_id IS NULL", name="ck_improvement_platform_scope"),
        sa.CheckConstraint(
            "baseline_prompt_version_id <> draft_prompt_version_id",
            name="ck_improvement_distinct_prompt_versions",
        ),
    )
    op.create_index("ix_improvement_candidate_created", _TABLE, ["created_at"])
    _replace_kinds(_NEW_KINDS)
    op.execute(
        "CREATE TRIGGER feedback_immutable BEFORE UPDATE OR DELETE ON governed_change_candidates "
        "FOR EACH ROW EXECUTE FUNCTION prufai_feedback_immutable()"
    )
    op.execute("""
    CREATE FUNCTION prufai_improvement_draft_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM governed_change_candidates WHERE draft_prompt_version_id = OLD.id
      ) THEN
        RAISE EXCEPTION 'GOVERNED_IMPROVEMENT_DRAFT_IMMUTABLE';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    """)
    op.execute(
        "CREATE TRIGGER improvement_draft_immutable BEFORE UPDATE OR DELETE ON prompt_versions "
        "FOR EACH ROW EXECUTE FUNCTION prufai_improvement_draft_immutable()"
    )


def downgrade() -> None:
    # The prior schema cannot represent sealed candidate operation receipts.
    # The old constraint refuses downgrade when any such history exists.
    _replace_kinds(_OLD_KINDS)
    op.execute("DROP TRIGGER improvement_draft_immutable ON prompt_versions")
    op.execute("DROP FUNCTION prufai_improvement_draft_immutable()")
    op.drop_table(_TABLE)
