"""Persist immutable LiD blueprint, pool and generated form receipts."""

import sqlalchemy as sa

from alembic import op

revision = "0039_lid_practice_provenance"
down_revision = "0038_dtz_preview_provenance"
branch_labels = None
depends_on = None

_DRAFT = "standard_pack_lid_provenance"
_POOLS = "lid_practice_pool_versions"
_FORMS = "lid_practice_forms"
_RECEIPTS = (_DRAFT, _POOLS, _FORMS)
_CHILDREN = (
    "standard_packs",
    "standard_pack_versions",
    "standard_pack_modules",
    "standard_pack_sections",
    "standard_pack_task_specs",
    "standard_pack_source_bindings",
    "standard_pack_rule_bindings",
    "rule_definitions",
    "rule_versions",
    "rule_evidence_bindings",
    "exam_versions",
    "exam_version_modules",
    "exam_version_sections",
    "exam_version_tasks",
    "exam_version_item_bindings",
)


def upgrade() -> None:
    op.create_table(
        _DRAFT,
        sa.Column(
            "standard_pack_version_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("manifest_checksum", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.CheckConstraint("length(manifest_checksum) = 64", name="ck_lid_manifest_digest"),
    )
    op.create_table(
        _POOLS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organisation_id",
            sa.Uuid(),
            sa.ForeignKey("organisations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "practice_pack_version_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("jurisdiction", sa.String(5), nullable=False),
        sa.Column("version_label", sa.String(80), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "practice_pack_version_id",
            "jurisdiction",
            "version_label",
            name="uq_lid_practice_pool_version",
        ),
        sa.CheckConstraint(
            "(scope = 'GENERAL_DE' AND jurisdiction = 'DE') OR "
            "(scope = 'BUNDESLAND_SPECIFIC' AND jurisdiction LIKE 'DE-__')",
            name="ck_lid_practice_pool_scope",
        ),
    )
    op.create_table(
        _FORMS,
        sa.Column(
            "exam_version_id",
            sa.Uuid(),
            sa.ForeignKey("exam_versions.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "blueprint_version_id",
            sa.Uuid(),
            sa.ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "general_pool_version_id",
            sa.Uuid(),
            sa.ForeignKey(f"{_POOLS}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "state_pool_version_id",
            sa.Uuid(),
            sa.ForeignKey(f"{_POOLS}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("bundesland", sa.String(5), nullable=False),
        sa.Column("selection_version", sa.String(48), nullable=False),
        sa.Column("selection_seed", sa.String(64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("""
    CREATE FUNCTION prufai_lid_receipt_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'LID_PRACTICE_HISTORY_IMMUTABLE';
    END $$;
    CREATE FUNCTION prufai_lid_attempt_pin_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (OLD.runtime_plan::jsonb ? 'lid_practice') OR EXISTS (
          SELECT 1 FROM lid_practice_forms WHERE exam_version_id = OLD.exam_version_id
      ) THEN
        IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'LID_PRACTICE_HISTORY_IMMUTABLE'; END IF;
        IF ROW(OLD.id, OLD.assignment_id, OLD.candidate_user_id, OLD.organisation_id,
               OLD.exam_version_id, OLD.attempt_number, OLD.delivery_manifest_checksum,
               OLD.runtime_plan_checksum, OLD.runtime_plan::jsonb, OLD.created_at)
          IS DISTINCT FROM
           ROW(NEW.id, NEW.assignment_id, NEW.candidate_user_id, NEW.organisation_id,
               NEW.exam_version_id, NEW.attempt_number, NEW.delivery_manifest_checksum,
               NEW.runtime_plan_checksum, NEW.runtime_plan::jsonb, NEW.created_at)
          OR (OLD.started_at IS NOT NULL AND OLD.started_at IS DISTINCT FROM NEW.started_at)
          OR (OLD.deadline_at IS NOT NULL AND OLD.deadline_at IS DISTINCT FROM NEW.deadline_at)
          OR (OLD.submitted_at IS NOT NULL AND OLD.submitted_at IS DISTINCT FROM NEW.submitted_at)
          THEN RAISE EXCEPTION 'LID_PRACTICE_HISTORY_IMMUTABLE'; END IF;
      ELSIF TG_OP = 'UPDATE' AND ((NEW.runtime_plan::jsonb ? 'lid_practice') OR EXISTS (
          SELECT 1 FROM lid_practice_forms WHERE exam_version_id = NEW.exam_version_id
      )) THEN
        RAISE EXCEPTION 'LID_PRACTICE_HISTORY_IMMUTABLE';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER lid_attempt_pin_immutable BEFORE UPDATE OR DELETE ON candidate_exam_attempts
      FOR EACH ROW EXECUTE FUNCTION prufai_lid_attempt_pin_immutable();
    CREATE FUNCTION prufai_lid_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF left(OLD.event_type, 20) = 'standards.lid_draft.'
        OR left(OLD.event_type, 13) = 'lid.practice.' THEN
        RAISE EXCEPTION 'LID_PRACTICE_HISTORY_IMMUTABLE';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER lid_audit_immutable BEFORE UPDATE OR DELETE ON audit_events
      FOR EACH ROW EXECUTE FUNCTION prufai_lid_audit_immutable();
    """)
    for table in _RECEIPTS:
        op.execute(
            f"CREATE TRIGGER lid_receipt_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prufai_lid_receipt_immutable()"
        )
    op.execute("""
    CREATE FUNCTION prufai_lid_child_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
      candidate jsonb;
      row_id uuid;
      pack_id uuid;
      rule_id uuid;
      exam_id uuid;
      sealed boolean;
      lifecycle_fields text[];
    BEGIN
      IF TG_TABLE_NAME = 'exam_versions' AND TG_OP = 'UPDATE' THEN
        lifecycle_fields := CASE
          WHEN OLD.status = 'DRAFT' AND NEW.status = 'HUMAN_REVIEW' THEN ARRAY['status']
          WHEN OLD.status = 'HUMAN_REVIEW' AND NEW.status = 'APPROVED' THEN ARRAY['status']
          WHEN OLD.status = 'APPROVED' AND NEW.status = 'PUBLISHED'
            AND NEW.published_at IS NOT NULL AND NEW.published_by_user_id IS NOT NULL
            THEN ARRAY['status', 'published_at', 'published_by_user_id']
          WHEN OLD.status = 'PUBLISHED' AND NEW.status = 'ACTIVE'
            AND NEW.activated_at IS NOT NULL THEN ARRAY['status', 'activated_at']
          WHEN OLD.status IN ('PUBLISHED', 'ACTIVE') AND NEW.status = 'RETIRED'
            THEN ARRAY['status', 'retired_at']
          ELSE NULL
        END;
        IF lifecycle_fields IS NOT NULL
          AND to_jsonb(OLD) - lifecycle_fields = to_jsonb(NEW) - lifecycle_fields
          AND EXISTS (SELECT 1 FROM lid_practice_forms WHERE exam_version_id = OLD.id)
          THEN RETURN NEW; END IF;
      END IF;
      FOR candidate IN SELECT value FROM jsonb_array_elements(jsonb_build_array(
        CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
        CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END
      )) LOOP
        IF candidate = 'null'::jsonb THEN CONTINUE; END IF;
        row_id := (candidate->>'id')::uuid;
        pack_id := NULL;
        rule_id := NULL;
        exam_id := NULL;
        sealed := false;
        CASE TG_TABLE_NAME
          WHEN 'standard_packs' THEN
            SELECT EXISTS (
              SELECT 1 FROM standard_pack_versions v WHERE v.standard_pack_id = row_id AND (
                EXISTS (SELECT 1 FROM standard_pack_lid_provenance p
                        WHERE p.standard_pack_version_id = v.id)
                OR EXISTS (SELECT 1 FROM lid_practice_pool_versions p
                           WHERE p.practice_pack_version_id = v.id)
              )
            ) INTO sealed;
          WHEN 'standard_pack_versions' THEN
            pack_id := row_id;
            sealed := (candidate->>'configuration_schema_version') = 's3-lid-draft-v1'
              AND (candidate->>'status') <> 'DRAFT';
          WHEN 'standard_pack_sections' THEN
            SELECT standard_pack_version_id INTO pack_id FROM standard_pack_modules
              WHERE id = (candidate->>'module_id')::uuid;
          WHEN 'standard_pack_task_specs' THEN
            SELECT m.standard_pack_version_id INTO pack_id
              FROM standard_pack_sections s JOIN standard_pack_modules m ON s.module_id = m.id
              WHERE s.id = (candidate->>'section_id')::uuid;
          WHEN 'rule_definitions' THEN
            SELECT EXISTS (
              SELECT 1 FROM rule_versions r JOIN standard_pack_lid_provenance p
                ON p.manifest::jsonb->'rules'
                   @> jsonb_build_array(jsonb_build_object('id', r.id::text))
              WHERE r.rule_definition_id = row_id
            ) INTO sealed;
          WHEN 'rule_versions' THEN
            rule_id := row_id;
          WHEN 'rule_evidence_bindings' THEN
            rule_id := (candidate->>'rule_version_id')::uuid;
          WHEN 'standard_pack_rule_bindings' THEN
            pack_id := (candidate->>'standard_pack_version_id')::uuid;
            SELECT EXISTS (
              SELECT 1 FROM rule_versions r JOIN rule_definitions d ON r.rule_definition_id = d.id
              WHERE r.id = (candidate->>'rule_version_id')::uuid
                AND left(upper(btrim(d.code)), 14) = 'PRUFAI_LID_S3_'
            ) INTO sealed;
          WHEN 'exam_versions' THEN
            exam_id := row_id;
            SELECT EXISTS (
              SELECT 1 FROM standard_pack_versions v
              WHERE v.id = (candidate->>'standard_pack_version_id')::uuid
                AND v.configuration_schema_version = 's3-lid-draft-v1'
            ) INTO sealed;
          WHEN 'exam_version_modules' THEN
            exam_id := (candidate->>'exam_version_id')::uuid;
          WHEN 'exam_version_sections' THEN
            SELECT exam_version_id INTO exam_id FROM exam_version_modules
              WHERE id = (candidate->>'exam_version_module_id')::uuid;
          WHEN 'exam_version_tasks' THEN
            SELECT m.exam_version_id INTO exam_id FROM exam_version_sections s
              JOIN exam_version_modules m ON m.id = s.exam_version_module_id
              WHERE s.id = (candidate->>'exam_version_section_id')::uuid;
          WHEN 'exam_version_item_bindings' THEN
            SELECT m.exam_version_id INTO exam_id FROM exam_version_tasks t
              JOIN exam_version_sections s ON s.id = t.exam_version_section_id
              JOIN exam_version_modules m ON m.id = s.exam_version_module_id
              WHERE t.id = (candidate->>'exam_version_task_id')::uuid;
          ELSE
            pack_id := (candidate->>'standard_pack_version_id')::uuid;
        END CASE;
        IF sealed OR EXISTS (SELECT 1 FROM standard_pack_lid_provenance
                             WHERE standard_pack_version_id = pack_id)
          OR EXISTS (SELECT 1 FROM lid_practice_pool_versions
                     WHERE practice_pack_version_id = pack_id)
          OR EXISTS (SELECT 1 FROM lid_practice_forms WHERE exam_version_id = exam_id)
          OR EXISTS (SELECT 1 FROM standard_pack_lid_provenance p WHERE p.manifest::jsonb->'rules'
                     @> jsonb_build_array(jsonb_build_object('id', rule_id::text)))
          THEN RAISE EXCEPTION 'LID_PRACTICE_HISTORY_IMMUTABLE'; END IF;
      END LOOP;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    """)
    for table in _CHILDREN:
        op.execute(
            f"CREATE TRIGGER lid_child_immutable BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prufai_lid_child_immutable()"
        )


def downgrade() -> None:
    for table in reversed(_CHILDREN):
        op.execute(f"DROP TRIGGER lid_child_immutable ON {table}")
    op.execute("DROP FUNCTION prufai_lid_child_immutable()")
    op.execute("DROP TRIGGER lid_attempt_pin_immutable ON candidate_exam_attempts")
    op.execute("DROP FUNCTION prufai_lid_attempt_pin_immutable()")
    op.execute("DROP TRIGGER lid_audit_immutable ON audit_events")
    op.execute("DROP FUNCTION prufai_lid_audit_immutable()")
    for table in reversed(_RECEIPTS):
        op.execute(f"DROP TRIGGER lid_receipt_immutable ON {table}")
        op.drop_table(table)
    op.execute("DROP FUNCTION prufai_lid_receipt_immutable()")
