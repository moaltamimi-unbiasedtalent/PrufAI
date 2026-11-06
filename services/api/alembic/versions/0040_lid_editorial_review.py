"""Attach immutable Berlin editorial provenance and review decisions."""

import sqlalchemy as sa

from alembic import op

revision = "0040_lid_editorial_review"
down_revision = "0039_lid_practice_provenance"
branch_labels = None
depends_on = None

COLLECTIONS = "lid_editorial_collections"
ITEMS = "lid_editorial_item_versions"
REVIEWS = "lid_editorial_review_receipts"
PUBLICATIONS = "lid_editorial_publications"
SEALED = (COLLECTIONS, ITEMS, REVIEWS, PUBLICATIONS)
NATIVE = (
    "question_bank_items",
    "question_bank_item_versions",
    "question_bank_item_evidence_bindings",
    "question_bank_standard_pack_bindings",
    "question_bank_item_cefr_levels",
    "human_review_requests",
    "standard_packs",
    "standard_pack_versions",
    "standard_pack_modules",
    "standard_pack_sections",
    "standard_pack_task_specs",
    "standard_pack_source_bindings",
    "standard_pack_rule_bindings",
    "audit_events",
)


def _foreign(name: str, table: str, *, unique: bool = False, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.Uuid(),
        sa.ForeignKey(f"{table}.id", ondelete="RESTRICT"),
        nullable=nullable,
        unique=unique,
    )


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), primary_key=True)


def _digest(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.String(64), nullable=nullable)


def _created(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        COLLECTIONS,
        _id(),
        _foreign("organisation_id", "organisations"),
        _foreign("pack_version_id", "standard_pack_versions", unique=True),
        _foreign("blueprint_version_id", "standard_pack_versions"),
        sa.Column("operation_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        _foreign("created_by_user_id", "users"),
        _created(),
        sa.Column("manifest", sa.JSON(), nullable=False),
        _digest("checksum"),
        sa.UniqueConstraint("organisation_id", "code", name="uq_lid_editorial_collection_scope"),
    )
    op.create_table(
        ITEMS,
        sa.Column(
            "item_version_id",
            sa.Uuid(),
            sa.ForeignKey(
                "question_bank_item_versions.id",
                ondelete="RESTRICT",
            ),
            primary_key=True,
        ),
        _foreign("item_id", "question_bank_items"),
        _foreign("collection_id", COLLECTIONS),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("jurisdiction", sa.String(5), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        _digest("checksum"),
        _created(),
        sa.CheckConstraint("ordinal > 0", name="ck_lid_editorial_ordinal"),
        sa.CheckConstraint(
            "(scope = 'GENERAL_DE' AND jurisdiction = 'DE') OR "
            "(scope = 'BUNDESLAND_SPECIFIC' AND jurisdiction = 'DE-BE')",
            name="ck_lid_editorial_jurisdiction",
        ),
    )
    op.create_index("ix_lid_editorial_item_versions_item_id", ITEMS, ["item_id"])
    op.create_index("ix_lid_editorial_item_versions_collection_id", ITEMS, ["collection_id"])
    op.create_table(
        REVIEWS,
        _id(),
        _foreign("item_version_id", "question_bank_item_versions"),
        _foreign("review_request_id", "human_review_requests"),
        _foreign("actor_user_id", "users"),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("evidence_class", sa.String(32), nullable=False),
        sa.Column("authority", sa.String(64), nullable=False),
        _digest("item_checksum"),
        _digest("provenance_checksum"),
        _digest("request_checksum"),
        _digest("checksum"),
        _foreign("qualification_id", "evaluation_examiner_qualifications", nullable=True),
        _digest("qualification_checksum", nullable=True),
        _digest("source_rights_checksum", nullable=True),
        sa.Column("reason", sa.Text()),
        sa.Column("rights", sa.JSON(none_as_null=True)),
        _created("recorded_at"),
        sa.UniqueConstraint("operation_id", name="uq_lid_editorial_review_operation"),
        sa.UniqueConstraint("item_version_id", "action", name="uq_lid_editorial_review_action"),
        sa.CheckConstraint(
            "action IN ('SUBMIT', 'FACTUAL', 'ASSESSMENT', 'RIGHTS')",
            name="ck_lid_editorial_review_action",
        ),
        sa.CheckConstraint(
            "(action = 'SUBMIT' AND decision = 'SUBMIT' "
            "AND evidence_class = 'AUTHENTICATED_SUBMISSION' "
            "AND qualification_id IS NULL AND qualification_checksum IS NULL "
            "AND source_rights_checksum IS NULL AND reason IS NULL) OR "
            "(action <> 'SUBMIT' AND decision IN ('APPROVE', 'REJECT') "
            "AND evidence_class IN ('SYNTHETIC_TEST', 'VERIFIED_CREDENTIAL') "
            "AND qualification_id IS NOT NULL AND qualification_checksum IS NOT NULL "
            "AND reason IS NOT NULL AND "
            "(decision = 'REJECT' OR source_rights_checksum IS NOT NULL))",
            name="ck_lid_editorial_review_evidence",
        ),
    )
    op.create_table(
        PUBLICATIONS,
        _id(),
        _foreign("collection_id", COLLECTIONS),
        _foreign("exam_version_id", "exam_versions", nullable=True, unique=True),
        sa.Column("operation_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        _foreign("actor_user_id", "users"),
        _digest("eligibility_checksum"),
        sa.Column("manifest", sa.JSON(), nullable=False),
        _digest("checksum"),
        _created(),
        sa.UniqueConstraint("collection_id", "action", name="uq_lid_editorial_publication_action"),
        sa.CheckConstraint(
            "(action = 'APPROVED' AND exam_version_id IS NULL) OR "
            "(action = 'PUBLISHED' AND exam_version_id IS NOT NULL)",
            name="ck_lid_editorial_publication_action",
        ),
    )
    op.execute("""
    CREATE FUNCTION prufai_lid_editorial_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'LID_EDITORIAL_HISTORY_IMMUTABLE';
    END $$;
    CREATE FUNCTION prufai_lid_editorial_native() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
      doc jsonb;
      obj uuid;
      pack uuid;
      sealed boolean := false;
      lifecycle text[] := ARRAY['status', 'reviewed_at', 'reviewed_by_user_id', 'approved_at',
        'approved_by_user_id', 'published_at', 'published_by_user_id',
        'activated_at', 'retired_at'];
    BEGIN
      doc := to_jsonb(OLD);
      obj := (doc->>'id')::uuid;
      CASE TG_TABLE_NAME
        WHEN 'question_bank_items' THEN
          SELECT EXISTS(SELECT 1 FROM lid_editorial_item_versions WHERE item_id = obj)
          INTO sealed;
        WHEN 'question_bank_item_versions' THEN
          SELECT EXISTS(SELECT 1 FROM lid_editorial_item_versions WHERE item_version_id = obj)
          INTO sealed;
          IF sealed AND TG_OP = 'UPDATE' AND doc - lifecycle = to_jsonb(NEW) - lifecycle
            THEN RETURN NEW; END IF;
        WHEN 'question_bank_item_evidence_bindings', 'question_bank_standard_pack_bindings',
             'question_bank_item_cefr_levels' THEN
          SELECT EXISTS(SELECT 1 FROM lid_editorial_item_versions
                        WHERE item_version_id = (doc->>'item_version_id')::uuid) INTO sealed;
        WHEN 'human_review_requests' THEN
          sealed := OLD.review_type = 'LID_EDITORIAL_CONTENT';
          lifecycle := ARRAY['status','decided_by','decided_at','decision','reason','updated_at'];
          IF sealed AND TG_OP = 'UPDATE' AND OLD.status IN ('PENDING', 'IN_REVIEW')
            AND doc - lifecycle = to_jsonb(NEW) - lifecycle THEN RETURN NEW; END IF;
        WHEN 'standard_packs' THEN
          SELECT EXISTS(SELECT 1 FROM standard_pack_versions v JOIN lid_editorial_collections c
                        ON c.pack_version_id = v.id WHERE v.standard_pack_id = obj) INTO sealed;
          IF sealed AND TG_OP = 'UPDATE'
            AND doc - ARRAY['status','updated_at'] = to_jsonb(NEW) - ARRAY['status','updated_at']
            THEN RETURN NEW; END IF;
        WHEN 'standard_pack_versions' THEN
          SELECT EXISTS(SELECT 1 FROM lid_editorial_collections WHERE pack_version_id = obj)
          INTO sealed;
          IF sealed AND TG_OP = 'UPDATE' AND doc - lifecycle = to_jsonb(NEW) - lifecycle
            THEN RETURN NEW; END IF;
        WHEN 'standard_pack_sections' THEN
          SELECT standard_pack_version_id INTO pack FROM standard_pack_modules
          WHERE id = (doc->>'module_id')::uuid;
        WHEN 'standard_pack_task_specs' THEN
          SELECT m.standard_pack_version_id INTO pack FROM standard_pack_sections s
          JOIN standard_pack_modules m ON m.id = s.module_id
          WHERE s.id = (doc->>'section_id')::uuid;
        WHEN 'audit_events' THEN
          sealed := left(OLD.event_type, 14) = 'lid.editorial.';
        ELSE
          pack := (doc->>'standard_pack_version_id')::uuid;
      END CASE;
      IF sealed OR EXISTS(SELECT 1 FROM lid_editorial_collections WHERE pack_version_id = pack)
        THEN RAISE EXCEPTION 'LID_EDITORIAL_HISTORY_IMMUTABLE'; END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    """)
    for table in SEALED:
        op.execute(
            f"CREATE TRIGGER lid_editorial_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prufai_lid_editorial_immutable()"
        )
    for table in NATIVE:
        op.execute(
            f"CREATE TRIGGER lid_editorial_native BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prufai_lid_editorial_native()"
        )


def downgrade() -> None:
    for table in NATIVE:
        op.execute(f"DROP TRIGGER lid_editorial_native ON {table}")
    op.execute("DROP FUNCTION prufai_lid_editorial_native()")
    for table in reversed(SEALED):
        op.execute(f"DROP TRIGGER lid_editorial_immutable ON {table}")
        op.drop_table(table)
    op.execute("DROP FUNCTION prufai_lid_editorial_immutable()")
