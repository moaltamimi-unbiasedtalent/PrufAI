"""Independent expert reference provenance and immutable human decisions."""

import sqlalchemy as sa

from alembic import op

revision = "0031_expert_references"
down_revision = "0030_evaluation_foundation"
branch_labels = None
depends_on = None


def _id(name: str, target: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Uuid(), sa.ForeignKey(target, ondelete="RESTRICT"), nullable=nullable)


def _history() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _approval() -> list[sa.Column]:
    return [
        sa.Column("status", sa.String(30), nullable=False),
        _id("created_by", "users.id"),
        _id("approved_by", "users.id", True),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approval_reference", sa.String(200)),
        sa.CheckConstraint("status IN ('PENDING_APPROVAL','APPROVED')"),
        sa.CheckConstraint("approved_by IS NULL OR approved_by <> created_by"),
    ]


def upgrade() -> None:
    op.create_table(
        "evaluation_references",
        *_history(),
        *_approval(),
        _id("dataset_id", "evaluation_datasets.id"),
        _id("organisation_id", "organisations.id", True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("split", sa.String(20), nullable=False),
        sa.Column("evidence_class", sa.String(30), nullable=False),
        sa.Column("content_checksum", sa.String(64), nullable=False),
        sa.Column("lineage_checksum", sa.String(64), nullable=False),
        sa.UniqueConstraint("dataset_id", "checksum", name="uq_evaluation_reference_version"),
        sa.CheckConstraint("split IN ('TRAIN','CALIBRATION','VALIDATION','HELD_OUT')"),
        sa.CheckConstraint("evidence_class IN ('SYNTHETIC_TEST','APPROVED_REFERENCE')"),
    )
    op.create_index("ix_reference_content", "evaluation_references", ["content_checksum", "split"])
    op.create_index("ix_reference_lineage", "evaluation_references", ["lineage_checksum", "split"])
    op.create_table(
        "evaluation_examiner_qualifications",
        *_history(),
        *_approval(),
        _id("examiner_id", "users.id"),
        _id("organisation_id", "organisations.id", True),
        sa.Column("evidence_class", sa.String(30), nullable=False),
        sa.CheckConstraint("approved_by IS NULL OR approved_by <> examiner_id"),
        sa.CheckConstraint("evidence_class IN ('SYNTHETIC_TEST','VERIFIED_CREDENTIAL')"),
    )
    op.create_table(
        "evaluation_expert_judgments",
        *_history(),
        _id("reference_id", "evaluation_references.id"),
        sa.Column("reference_checksum", sa.String(64), nullable=False),
        _id("examiner_id", "users.id"),
        _id("qualification_id", "evaluation_examiner_qualifications.id"),
        sa.Column("qualification_checksum", sa.String(64), nullable=False),
        sa.UniqueConstraint("reference_id", "examiner_id", name="uq_expert_independent_judgment"),
    )
    op.create_table(
        "evaluation_adjudications",
        *_history(),
        _id("reference_id", "evaluation_references.id"),
        sa.Column("reference_checksum", sa.String(64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        _id("supersedes_id", "evaluation_adjudications.id", True),
        _id("adjudicator_id", "users.id"),
        _id("qualification_id", "evaluation_examiner_qualifications.id"),
        sa.UniqueConstraint("reference_id", "version_number", name="uq_adjudication_version"),
    )
    op.execute("""
    CREATE FUNCTION prufai_reference_approval() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'EVALUATION_REFERENCE_HISTORY_IMMUTABLE'; END IF;
      IF to_jsonb(NEW)=to_jsonb(OLD) THEN RETURN NEW; END IF;
      IF OLD.status<>'PENDING_APPROVAL' OR NEW.status<>'APPROVED'
        OR NEW.approved_by IS NULL OR NEW.approved_by=OLD.created_by
        OR NEW.approved_at IS NULL OR NEW.approval_reference IS NULL
        OR (to_jsonb(NEW)-ARRAY['status','approved_by','approved_at','approval_reference'])
          IS DISTINCT FROM
            (to_jsonb(OLD)-ARRAY['status','approved_by','approved_at','approval_reference'])
      THEN RAISE EXCEPTION 'EVALUATION_REFERENCE_HISTORY_IMMUTABLE'; END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION prufai_reference_split() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM pg_advisory_xact_lock(hashtextextended('evaluation-split',0));
      IF EXISTS (SELECT 1 FROM evaluation_references r
        WHERE r.split<>NEW.split
          AND (r.content_checksum=NEW.content_checksum
          OR r.lineage_checksum=NEW.lineage_checksum))
      THEN RAISE EXCEPTION 'EVALUATION_SPLIT_LEAKAGE'; END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION prufai_judgment_insert() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM 1 FROM evaluation_references r
        JOIN evaluation_examiner_qualifications q ON q.id=NEW.qualification_id
        WHERE r.id=NEW.reference_id AND r.status='APPROVED' AND r.checksum=NEW.reference_checksum
        AND r.created_by<>NEW.examiner_id AND q.examiner_id=NEW.examiner_id
        AND q.status='APPROVED' AND q.checksum=NEW.qualification_checksum
        AND q.organisation_id IS NOT DISTINCT FROM r.organisation_id
        AND (r.evidence_class='SYNTHETIC_TEST' OR q.evidence_class='VERIFIED_CREDENTIAL')
        FOR UPDATE OF r;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'EVALUATION_QUALIFIED_INDEPENDENT_EXAMINER_REQUIRED'; END IF;
      IF (SELECT count(*) FROM evaluation_expert_judgments WHERE reference_id=NEW.reference_id)>=2
        THEN RAISE EXCEPTION 'EVALUATION_REFERENCE_PROTOCOL_COMPLETE'; END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION prufai_adjudication_insert() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      PERFORM 1 FROM evaluation_references r
        JOIN evaluation_examiner_qualifications q ON q.id=NEW.qualification_id
        WHERE r.id=NEW.reference_id AND r.status='APPROVED'
        AND r.checksum=NEW.reference_checksum AND r.created_by<>NEW.adjudicator_id
        AND q.examiner_id=NEW.adjudicator_id AND q.status='APPROVED'
        AND q.organisation_id IS NOT DISTINCT FROM r.organisation_id
        AND (r.evidence_class='SYNTHETIC_TEST' OR q.evidence_class='VERIFIED_CREDENTIAL')
        FOR UPDATE OF r;
      IF NOT FOUND OR EXISTS (SELECT 1 FROM evaluation_expert_judgments
        WHERE reference_id=NEW.reference_id AND examiner_id=NEW.adjudicator_id)
        OR (SELECT count(*) FROM evaluation_expert_judgments
            WHERE reference_id=NEW.reference_id)<>2
        OR jsonb_array_length(NEW.body::jsonb->'judgment_pins')<>2
        OR (SELECT count(DISTINCT j.id)
          FROM jsonb_array_elements(NEW.body::jsonb->'judgment_pins') p
          JOIN evaluation_expert_judgments j ON j.id::text=p->>'id'
          AND j.checksum=p->>'checksum' AND j.reference_id=NEW.reference_id)<>2
      THEN RAISE EXCEPTION 'EVALUATION_INDEPENDENT_EXACT_ADJUDICATION_REQUIRED'; END IF;
      IF NEW.version_number=1 THEN
        IF NEW.supersedes_id IS NOT NULL THEN
          RAISE EXCEPTION 'EVALUATION_ADJUDICATION_SUCCESSOR_CONFLICT'; END IF;
      ELSIF NOT EXISTS (SELECT 1 FROM evaluation_adjudications a
        WHERE a.id=NEW.supersedes_id AND a.reference_id=NEW.reference_id
        AND a.version_number=NEW.version_number-1) THEN
        RAISE EXCEPTION 'EVALUATION_ADJUDICATION_SUCCESSOR_CONFLICT';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER evaluation_adjudication_insert
      BEFORE INSERT ON evaluation_adjudications
      FOR EACH ROW EXECUTE FUNCTION prufai_adjudication_insert();
    CREATE TRIGGER evaluation_reference_approval
      BEFORE UPDATE OR DELETE ON evaluation_references
      FOR EACH ROW EXECUTE FUNCTION prufai_reference_approval();
    CREATE TRIGGER evaluation_qualification_approval
      BEFORE UPDATE OR DELETE ON evaluation_examiner_qualifications
      FOR EACH ROW EXECUTE FUNCTION prufai_reference_approval();
    CREATE TRIGGER evaluation_reference_split
      BEFORE INSERT ON evaluation_references
      FOR EACH ROW EXECUTE FUNCTION prufai_reference_split();
    CREATE TRIGGER evaluation_judgment_insert
      BEFORE INSERT ON evaluation_expert_judgments
      FOR EACH ROW EXECUTE FUNCTION prufai_judgment_insert();
    CREATE TRIGGER evaluation_judgment_immutable
      BEFORE UPDATE OR DELETE ON evaluation_expert_judgments
      FOR EACH ROW EXECUTE FUNCTION prufai_evaluation_immutable();
    CREATE TRIGGER evaluation_adjudication_immutable
      BEFORE UPDATE OR DELETE ON evaluation_adjudications
      FOR EACH ROW EXECUTE FUNCTION prufai_evaluation_immutable();
    """)


def downgrade() -> None:
    for table in (
        "evaluation_adjudications",
        "evaluation_expert_judgments",
        "evaluation_examiner_qualifications",
        "evaluation_references",
    ):
        op.drop_table(table)
    for function in (
        "prufai_adjudication_insert",
        "prufai_judgment_insert",
        "prufai_reference_split",
        "prufai_reference_approval",
    ):
        op.execute(f"DROP FUNCTION {function}()")
