"""Governed golden datasets, reproducible evaluation and independent quality review."""

import sqlalchemy as sa

from alembic import op

revision = "0030_evaluation_foundation"
down_revision = "0029_speaking_execution"
branch_labels = None
depends_on = None


def _install_guards() -> None:
    # These triggers concern only new evaluation tables. Existing assessment,
    # registry, audit and candidate tables are never rewritten.
    op.execute("""
    CREATE FUNCTION prufai_evaluation_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'EVALUATION_HISTORY_IMMUTABLE'; END IF;
      IF to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
        RAISE EXCEPTION 'EVALUATION_HISTORY_IMMUTABLE'; END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION prufai_evaluation_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE allowed text[];
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'EVALUATION_HISTORY_IMMUTABLE'; END IF;
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
      ELSE RAISE EXCEPTION 'EVALUATION_LIFECYCLE_CONFLICT'; END IF;
      IF (to_jsonb(NEW)-allowed) IS DISTINCT FROM (to_jsonb(OLD)-allowed) THEN
        RAISE EXCEPTION 'EVALUATION_HISTORY_IMMUTABLE';
      END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION prufai_evaluation_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR OLD.status<>'RUNNING' THEN
        IF TG_OP='UPDATE' AND to_jsonb(NEW) IS NOT DISTINCT FROM to_jsonb(OLD)
          THEN RETURN NEW; END IF;
        RAISE EXCEPTION 'EVALUATION_HISTORY_IMMUTABLE';
      END IF;
      IF (to_jsonb(NEW)-ARRAY['status','hard_gate','metrics','limitations',
          'failure_count','checksum','completed_at'])
        IS DISTINCT FROM
        (to_jsonb(OLD)-ARRAY['status','hard_gate','metrics','limitations',
          'failure_count','checksum','completed_at']) THEN
        RAISE EXCEPTION 'EVALUATION_PROVENANCE_IMMUTABLE';
      END IF;
      IF NEW.status NOT IN ('COMPLETED','FAILED') OR NEW.completed_at IS NULL
        OR NEW.checksum IS NULL THEN
        RAISE EXCEPTION 'EVALUATION_COMPLETION_REQUIRED';
      END IF;
      IF (SELECT count(*) FROM evaluation_case_results WHERE run_id=NEW.id)<>NEW.case_count THEN
        RAISE EXCEPTION 'EVALUATION_COMPLETION_CASE_COUNT_MISMATCH';
      END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION prufai_evaluation_result_insert() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM 1 FROM evaluation_runs WHERE id=NEW.run_id AND status='RUNNING' FOR UPDATE;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'EVALUATION_COMPLETED_RESULTS_IMMUTABLE';
      END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION prufai_evaluation_review_insert() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM evaluation_runs r
          JOIN evaluation_dataset_versions v ON v.id=r.dataset_version_id
        WHERE r.id=NEW.run_id AND r.status IN ('COMPLETED','FAILED')
          AND r.checksum=NEW.run_checksum AND r.created_by<>NEW.reviewer_user_id
          AND v.created_by<>NEW.reviewer_user_id
          AND (NEW.decision<>'ACCEPTED' OR r.hard_gate='PASS')
      ) THEN RAISE EXCEPTION 'EVALUATION_INDEPENDENT_REVIEW_REQUIRED'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER evaluation_dataset_immutable BEFORE UPDATE OR DELETE ON evaluation_datasets
      FOR EACH ROW EXECUTE FUNCTION prufai_evaluation_immutable();
    CREATE TRIGGER evaluation_version_immutable
      BEFORE UPDATE OR DELETE ON evaluation_dataset_versions
      FOR EACH ROW EXECUTE FUNCTION prufai_evaluation_version_guard();
    CREATE TRIGGER evaluation_run_immutable BEFORE UPDATE OR DELETE ON evaluation_runs
      FOR EACH ROW EXECUTE FUNCTION prufai_evaluation_run_guard();
    CREATE TRIGGER evaluation_case_immutable BEFORE UPDATE OR DELETE ON evaluation_case_results
      FOR EACH ROW EXECUTE FUNCTION prufai_evaluation_immutable();
    CREATE TRIGGER evaluation_case_insert BEFORE INSERT ON evaluation_case_results
      FOR EACH ROW EXECUTE FUNCTION prufai_evaluation_result_insert();
    CREATE TRIGGER evaluation_review_immutable BEFORE UPDATE OR DELETE ON evaluation_reviews
      FOR EACH ROW EXECUTE FUNCTION prufai_evaluation_immutable();
    CREATE TRIGGER evaluation_review_insert BEFORE INSERT ON evaluation_reviews
      FOR EACH ROW EXECUTE FUNCTION prufai_evaluation_review_insert();
    """)


def _remove_guards() -> None:
    for table, triggers in (
        ("evaluation_datasets", ["evaluation_dataset_immutable"]),
        ("evaluation_dataset_versions", ["evaluation_version_immutable"]),
        ("evaluation_runs", ["evaluation_run_immutable"]),
        ("evaluation_case_results", ["evaluation_case_immutable", "evaluation_case_insert"]),
        ("evaluation_reviews", ["evaluation_review_immutable", "evaluation_review_insert"]),
    ):
        for trigger in triggers:
            op.execute(f"DROP TRIGGER {trigger} ON {table}")
    for name in ("immutable", "version_guard", "run_guard", "result_insert", "review_insert"):
        op.execute(f"DROP FUNCTION prufai_evaluation_{name}()")


def upgrade() -> None:
    op.create_table(
        "evaluation_datasets",
        sa.Column("code", sa.String(length=120), nullable=False),
        sa.Column(
            "organisation_id",
            sa.Uuid(),
            sa.ForeignKey("organisations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "evaluation_dataset_versions",
        sa.Column(
            "dataset_id",
            sa.Uuid(),
            sa.ForeignKey("evaluation_datasets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("assessment_family", sa.String(length=40), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=False),
        sa.Column("cefr_level", sa.String(length=10), nullable=True),
        sa.Column("skill", sa.String(length=30), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("cases", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("evidence_class", sa.String(length=30), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provenance_review_reference", sa.String(length=200), nullable=True),
        sa.Column("published_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by <> created_by",
            name="ck_evaluation_dataset_independent_approval",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','REVIEW_REQUIRED','APPROVED','PUBLISHED')",
            name="ck_evaluation_dataset_state",
        ),
        sa.CheckConstraint("version_number > 0", name="ck_evaluation_dataset_version_positive"),
        sa.UniqueConstraint("dataset_id", "version_number", name="uq_evaluation_dataset_version"),
    )
    op.create_index(
        "ix_evaluation_dataset_status",
        "evaluation_dataset_versions",
        ["status", "created_at"],
        unique=False,
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "dataset_version_id",
            sa.Uuid(),
            sa.ForeignKey("evaluation_dataset_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("dataset_checksum", sa.String(length=64), nullable=False),
        sa.Column("request_checksum", sa.String(length=64), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("configuration_checksum", sa.String(length=64), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("execution_class", sa.String(length=30), nullable=False),
        sa.Column("evidence_class", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("hard_gate", sa.String(length=20), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.UniqueConstraint("operation_id"),
        sa.CheckConstraint("hard_gate IN ('PENDING','PASS','FAIL')", name="ck_evaluation_run_gate"),
        sa.CheckConstraint(
            "status IN ('RUNNING','COMPLETED','FAILED')", name="ck_evaluation_run_state"
        ),
    )
    op.create_index(
        "ix_evaluation_runs_dataset_created",
        "evaluation_runs",
        ["dataset_version_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "evaluation_case_results",
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("evaluation_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("case_id", sa.String(length=120), nullable=False),
        sa.Column("input_checksum", sa.String(length=64), nullable=False),
        sa.Column("observation_checksum", sa.String(length=64), nullable=False),
        sa.Column("execution_reference", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.UniqueConstraint("run_id", "case_id", name="uq_evaluation_case_result"),
    )
    op.create_table(
        "evaluation_reviews",
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("evaluation_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("run_checksum", sa.String(length=64), nullable=False),
        sa.Column("reviewer_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("review_reference", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.CheckConstraint(
            "decision IN ('ACCEPTED','REJECTED','CHANGES_REQUIRED')",
            name="ck_evaluation_review_decision",
        ),
    )
    _install_guards()


def downgrade() -> None:
    _remove_guards()
    op.drop_table("evaluation_reviews")
    op.drop_table("evaluation_case_results")
    op.drop_table("evaluation_runs")
    op.drop_table("evaluation_dataset_versions")
    op.drop_table("evaluation_datasets")
