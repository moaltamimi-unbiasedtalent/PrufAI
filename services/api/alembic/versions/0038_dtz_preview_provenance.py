"""Attach immutable DTZ preview claims to the existing Standard Pack version."""

import sqlalchemy as sa

from alembic import op

revision = "0038_dtz_preview_provenance"
down_revision = "0037_b1_foundation"
branch_labels = None
depends_on = None

_TABLE = "standard_pack_dtz_provenance"
_CHILDREN = (
    "standard_pack_versions",
    "standard_packs",
    "standard_pack_modules",
    "standard_pack_sections",
    "standard_pack_task_specs",
    "standard_pack_source_bindings",
    "standard_pack_rule_bindings",
    "rule_definitions",
    "rule_versions",
    "rule_evidence_bindings",
    "assessment_rubrics",
    "rubric_versions",
    "rubric_evidence_bindings",
    "exam_versions",
)


def upgrade() -> None:
    op.create_table(
        _TABLE,
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
        sa.CheckConstraint("length(manifest_checksum) = 64", name="ck_dtz_manifest_digest"),
    )
    op.execute("""
    CREATE FUNCTION prufai_dtz_manifest_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'DTZ_PREVIEW_HISTORY_IMMUTABLE';
    END $$;
    CREATE TRIGGER dtz_manifest_immutable BEFORE UPDATE OR DELETE ON standard_pack_dtz_provenance
      FOR EACH ROW EXECUTE FUNCTION prufai_dtz_manifest_immutable();
    CREATE FUNCTION prufai_dtz_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.event_type IN ('standards.dtz_draft.created', 'standards.dtz_draft.previewed') THEN
        RAISE EXCEPTION 'DTZ_PREVIEW_HISTORY_IMMUTABLE';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER dtz_audit_immutable BEFORE UPDATE OR DELETE ON audit_events
      FOR EACH ROW EXECUTE FUNCTION prufai_dtz_audit_immutable();
    """)
    op.execute("""
    CREATE FUNCTION prufai_dtz_child_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
      candidate jsonb;
      row_id uuid;
      pack_id uuid;
      artifact_id uuid;
      sealed boolean;
    BEGIN
      -- Examine both endpoints of UPDATE so a row cannot be moved into or out of history.
      FOR candidate IN SELECT value FROM jsonb_array_elements(jsonb_build_array(
        CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
        CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END
      )) LOOP
        IF candidate = 'null'::jsonb THEN CONTINUE; END IF;
        row_id := (candidate->>'id')::uuid;
        pack_id := NULL;
        artifact_id := NULL;
        sealed := false;
        CASE TG_TABLE_NAME
          WHEN 'standard_pack_versions' THEN
            pack_id := row_id;
            sealed := (candidate->>'configuration_schema_version') = 's2-dtz-preview-v1'
              AND (candidate->>'status') <> 'DRAFT';
          WHEN 'standard_packs' THEN
            SELECT EXISTS (
              SELECT 1 FROM standard_pack_dtz_provenance e JOIN standard_pack_versions v
                ON e.standard_pack_version_id = v.id WHERE v.standard_pack_id = row_id
            ) INTO sealed;
          WHEN 'standard_pack_sections' THEN
            SELECT standard_pack_version_id INTO pack_id FROM standard_pack_modules
              WHERE id = (candidate->>'module_id')::uuid;
          WHEN 'standard_pack_task_specs' THEN
            SELECT m.standard_pack_version_id INTO pack_id
              FROM standard_pack_sections s JOIN standard_pack_modules m ON s.module_id = m.id
              WHERE s.id = (candidate->>'section_id')::uuid;
          WHEN 'rule_definitions' THEN
            SELECT EXISTS (
              SELECT 1 FROM standard_pack_dtz_provenance e JOIN rule_versions r
                ON (e.manifest::jsonb #> '{artifacts,rules}') @> jsonb_build_array(
                  jsonb_build_object('id',r.id::text))
              WHERE r.rule_definition_id = row_id
            ) INTO sealed;
          WHEN 'rule_versions', 'rule_evidence_bindings' THEN
            artifact_id := CASE WHEN TG_TABLE_NAME = 'rule_versions' THEN row_id
                                ELSE (candidate->>'rule_version_id')::uuid END;
            SELECT EXISTS (
              SELECT 1 FROM standard_pack_dtz_provenance e
              WHERE (e.manifest::jsonb #> '{artifacts,rules}') @> jsonb_build_array(
                jsonb_build_object('id',artifact_id::text))
            ) INTO sealed;
          WHEN 'assessment_rubrics' THEN
            SELECT EXISTS (
              SELECT 1 FROM standard_pack_dtz_provenance e JOIN rubric_versions r
                ON (e.manifest::jsonb #> '{artifacts,rubrics}') @> jsonb_build_array(
                  jsonb_build_object('id',r.id::text))
              WHERE r.rubric_id = row_id
            ) INTO sealed;
          WHEN 'standard_pack_rule_bindings' THEN
            pack_id := (candidate->>'standard_pack_version_id')::uuid;
            artifact_id := (candidate->>'rule_version_id')::uuid;
            SELECT EXISTS (
              SELECT 1 FROM rule_versions r JOIN rule_definitions d ON d.id = r.rule_definition_id
              WHERE r.id = artifact_id AND left(upper(btrim(d.code)), 14) = 'PRUFAI_DTZ_S2_'
            ) INTO sealed;
          WHEN 'rubric_evidence_bindings' THEN
            SELECT standard_pack_version_id INTO pack_id FROM rubric_versions
              WHERE id = (candidate->>'rubric_version_id')::uuid;
          WHEN 'exam_versions' THEN
            pack_id := (candidate->>'standard_pack_version_id')::uuid;
            SELECT EXISTS (
              SELECT 1 FROM standard_pack_versions v
              WHERE v.id = pack_id AND v.configuration_schema_version = 's2-dtz-preview-v1'
            ) INTO sealed;
          ELSE
            pack_id := (candidate->>'standard_pack_version_id')::uuid;
        END CASE;
        IF sealed OR EXISTS (
          SELECT 1 FROM standard_pack_dtz_provenance WHERE standard_pack_version_id = pack_id
        ) THEN
          RAISE EXCEPTION 'DTZ_PREVIEW_HISTORY_IMMUTABLE';
        END IF;
      END LOOP;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    """)
    for table in _CHILDREN:
        op.execute(
            f"CREATE TRIGGER dtz_child_immutable BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prufai_dtz_child_immutable()"
        )


def downgrade() -> None:
    for table in reversed(_CHILDREN):
        op.execute(f"DROP TRIGGER dtz_child_immutable ON {table}")
    op.execute("DROP FUNCTION prufai_dtz_child_immutable()")
    op.execute("DROP TRIGGER dtz_audit_immutable ON audit_events")
    op.execute("DROP FUNCTION prufai_dtz_audit_immutable()")
    op.execute("DROP TRIGGER dtz_manifest_immutable ON standard_pack_dtz_provenance")
    op.execute("DROP FUNCTION prufai_dtz_manifest_immutable()")
    op.drop_table(_TABLE)
