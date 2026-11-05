"""Governed regression lifecycle, review receipts and empirical threshold definitions."""

import sqlalchemy as sa

from alembic import op

revision = "0032_eval_regression"
down_revision = "0031_expert_references"
branch_labels = None
depends_on = None

STATES = "status IN ('DRAFT','REVIEW_REQUIRED','APPROVED','PUBLISHED'"
REJECTION = """
    (status = 'REJECTED' AND rejected_by IS NOT NULL AND rejected_by <> created_by
       AND rejected_at IS NOT NULL AND rejection_review_reference IS NOT NULL)
    OR (status <> 'REJECTED' AND rejected_by IS NULL AND rejected_at IS NULL
       AND rejection_review_reference IS NULL)
"""


def _version_guard(*, regression: bool) -> None:
    rejection = (
        """
      ELSIF OLD.status='REVIEW_REQUIRED' AND NEW.status='REJECTED'
        AND OLD.provenance->>'origin' IN ('DYNAMIC_REGRESSION_CASE','DYNAMIC_REGRESSION_SUITE')
        AND NEW.rejected_by<>OLD.created_by AND NEW.rejected_at IS NOT NULL
        AND NEW.rejection_review_reference IS NOT NULL THEN
        allowed := ARRAY['status','rejected_by','rejected_at','rejection_review_reference'];
    """
        if regression
        else ""
    )
    op.execute(f"""
    CREATE OR REPLACE FUNCTION prufai_evaluation_version_guard()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE allowed text[];
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'EVALUATION_HISTORY_IMMUTABLE'; END IF;
      IF to_jsonb(NEW) IS NOT DISTINCT FROM to_jsonb(OLD) THEN RETURN NEW; END IF;
      IF OLD.status='DRAFT' AND NEW.status='REVIEW_REQUIRED' AND NEW.submitted_at IS NOT NULL THEN
        allowed := ARRAY['status','submitted_at'];
      ELSIF OLD.status='REVIEW_REQUIRED' AND NEW.status='APPROVED'
        AND NEW.approved_at IS NOT NULL AND NEW.approved_by IS NOT NULL
        AND NEW.approved_by<>OLD.created_by AND NEW.provenance_review_reference IS NOT NULL THEN
        allowed := ARRAY['status','approved_at','approved_by','provenance_review_reference'];
      ELSIF OLD.status='APPROVED' AND NEW.status='PUBLISHED'
        AND NEW.published_at IS NOT NULL AND NEW.published_by IS NOT NULL THEN
        allowed := ARRAY['status','published_at','published_by'];
      {rejection}
      ELSE RAISE EXCEPTION 'EVALUATION_LIFECYCLE_CONFLICT'; END IF;
      IF (to_jsonb(NEW)-allowed) IS DISTINCT FROM (to_jsonb(OLD)-allowed) THEN
        RAISE EXCEPTION 'EVALUATION_HISTORY_IMMUTABLE';
      END IF;
      RETURN NEW;
    END $$;
    """)


def _review_guard(*, regression: bool) -> None:
    scope = (
        """
          AND CASE WHEN r.configuration->>'execution_adapter'='GOVERNED_REGRESSION_COMPARISON_V1'
            THEN NEW.operation_id IS NOT NULL
              AND jsonb_typeof(r.configuration::jsonb->'review_excluded_actor_ids')='array'
              AND NOT (r.configuration::jsonb->'review_excluded_actor_ids'
                       ? NEW.reviewer_user_id::text)
            ELSE NEW.operation_id IS NULL
          END
    """
        if regression
        else ""
    )
    op.execute(f"""
    CREATE OR REPLACE FUNCTION prufai_evaluation_review_insert()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM evaluation_runs r
          JOIN evaluation_dataset_versions v ON v.id=r.dataset_version_id
        WHERE r.id=NEW.run_id AND r.status IN ('COMPLETED','FAILED')
          AND r.checksum=NEW.run_checksum AND r.created_by<>NEW.reviewer_user_id
          AND v.created_by<>NEW.reviewer_user_id
          AND (NEW.decision<>'ACCEPTED' OR r.hard_gate='PASS')
          {scope}
      ) THEN RAISE EXCEPTION 'EVALUATION_INDEPENDENT_REVIEW_REQUIRED'; END IF;
      RETURN NEW;
    END $$;
    """)


def upgrade() -> None:
    op.add_column(
        "evaluation_dataset_versions",
        sa.Column("rejected_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column(
        "evaluation_dataset_versions",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "evaluation_dataset_versions",
        sa.Column("rejection_review_reference", sa.String(200), nullable=True),
    )
    op.drop_constraint("ck_evaluation_dataset_state", "evaluation_dataset_versions", type_="check")
    op.create_check_constraint(
        "ck_evaluation_dataset_state", "evaluation_dataset_versions", STATES + ",'REJECTED')"
    )
    op.create_check_constraint(
        "ck_evaluation_dataset_rejection", "evaluation_dataset_versions", REJECTION
    )
    op.add_column("evaluation_reviews", sa.Column("operation_id", sa.Uuid(), nullable=True))
    op.add_column("evaluation_reviews", sa.Column("request_checksum", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_evaluation_reviews_operation_id", "evaluation_reviews", ["operation_id"]
    )
    op.create_check_constraint(
        "ck_evaluation_review_operation",
        "evaluation_reviews",
        "(operation_id IS NULL) = (request_checksum IS NULL)",
    )
    op.create_table(
        "evaluation_threshold_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column(
            "suite_version_id",
            sa.Uuid(),
            sa.ForeignKey("evaluation_dataset_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("proposal_checksum", sa.String(64), nullable=False),
        sa.Column("approval", sa.JSON(none_as_null=True)),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(status='DRAFT' AND approval IS NULL AND approved_by IS NULL AND approved_at IS NULL)"
            " OR (status='APPROVED' AND approval IS NOT NULL AND approved_by IS NOT NULL"
            " AND approved_at IS NOT NULL AND approved_by<>created_by)",
            name="ck_evaluation_threshold_approval",
        ),
    )
    _version_guard(regression=True)
    _review_guard(regression=True)
    op.execute("""
    CREATE FUNCTION prufai_evaluation_regression_version_insert()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE origin text := NEW.provenance->>'origin';
    BEGIN
      IF origin IN ('DYNAMIC_REGRESSION_CASE','DYNAMIC_REGRESSION_SUITE') THEN
        IF NEW.status<>'DRAFT' OR NEW.submitted_at IS NOT NULL OR NEW.approved_by IS NOT NULL
          OR NEW.approved_at IS NOT NULL OR NEW.provenance_review_reference IS NOT NULL
          OR NEW.published_at IS NOT NULL OR NEW.published_by IS NOT NULL THEN
          RAISE EXCEPTION 'EVALUATION_REGRESSION_DRAFT_REQUIRED';
        END IF;
      END IF;
      IF EXISTS (
        SELECT 1 FROM evaluation_dataset_versions v
        WHERE v.dataset_id=NEW.dataset_id
          AND (v.provenance->>'origin' IN ('DYNAMIC_REGRESSION_CASE','DYNAMIC_REGRESSION_SUITE')
            OR origin IN ('DYNAMIC_REGRESSION_CASE','DYNAMIC_REGRESSION_SUITE'))
          AND v.provenance->>'origin' IS DISTINCT FROM origin
      ) THEN RAISE EXCEPTION 'EVALUATION_REGRESSION_DATASET_ORIGIN_IMMUTABLE'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER evaluation_regression_version_insert BEFORE INSERT ON evaluation_dataset_versions
      FOR EACH ROW EXECUTE FUNCTION prufai_evaluation_regression_version_insert();
    CREATE FUNCTION prufai_evaluation_threshold_guard()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'EVALUATION_HISTORY_IMMUTABLE'; END IF;
      IF TG_OP='INSERT' THEN
        IF NEW.status<>'DRAFT' OR NEW.checksum<>NEW.proposal_checksum THEN
          RAISE EXCEPTION 'EVALUATION_THRESHOLD_DRAFT_REQUIRED';
        END IF;
        RETURN NEW;
      END IF;
      IF to_jsonb(NEW) IS NOT DISTINCT FROM to_jsonb(OLD) THEN RETURN NEW; END IF;
      IF OLD.status<>'DRAFT' OR NEW.status<>'APPROVED'
        OR (to_jsonb(NEW)-ARRAY['status','approval','approved_by','approved_at','checksum'])
           IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['status','approval','approved_by','approved_at','checksum'])
      THEN RAISE EXCEPTION 'EVALUATION_HISTORY_IMMUTABLE'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER evaluation_threshold_guard BEFORE INSERT OR UPDATE OR DELETE
      ON evaluation_threshold_definitions FOR EACH ROW
      EXECUTE FUNCTION prufai_evaluation_threshold_guard();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER evaluation_threshold_guard ON evaluation_threshold_definitions")
    op.execute("DROP FUNCTION prufai_evaluation_threshold_guard()")
    op.execute("DROP TRIGGER evaluation_regression_version_insert ON evaluation_dataset_versions")
    op.execute("DROP FUNCTION prufai_evaluation_regression_version_insert()")
    op.drop_table("evaluation_threshold_definitions")
    _review_guard(regression=False)
    _version_guard(regression=False)
    op.drop_constraint("ck_evaluation_review_operation", "evaluation_reviews", type_="check")
    op.drop_constraint("uq_evaluation_reviews_operation_id", "evaluation_reviews", type_="unique")
    op.drop_column("evaluation_reviews", "request_checksum")
    op.drop_column("evaluation_reviews", "operation_id")
    op.drop_constraint(
        "ck_evaluation_dataset_rejection", "evaluation_dataset_versions", type_="check"
    )
    op.drop_constraint("ck_evaluation_dataset_state", "evaluation_dataset_versions", type_="check")
    op.create_check_constraint(
        "ck_evaluation_dataset_state", "evaluation_dataset_versions", STATES + ")"
    )
    for field in ("rejection_review_reference", "rejected_at", "rejected_by"):
        op.drop_column("evaluation_dataset_versions", field)
