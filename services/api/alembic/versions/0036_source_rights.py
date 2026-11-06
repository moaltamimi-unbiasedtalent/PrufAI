"""Seal source metadata, rights decisions and managed private object identities."""

import sqlalchemy as sa

from alembic import op

revision = "0036_source_rights"
down_revision = "0035_prompt_promotion"
branch_labels = None
depends_on = None

VERSIONS = "knowledge_document_versions"
RIGHTS = "knowledge_legal_metadata"
VERSION_COLUMNS = (
    sa.Column("content_mode", sa.String(32), nullable=True),
    sa.Column("metadata_checksum", sa.String(64), nullable=True),
    sa.Column("storage_bucket", sa.String(255), nullable=True),
)
RIGHTS_COLUMNS = (
    sa.Column("ingestion_allowed", sa.Boolean(), nullable=True),
    sa.Column("approved_sha256", sa.String(64), nullable=True),
    sa.Column("basis_reference", sa.String(240), nullable=True),
    sa.Column("retention_policy", sa.String(240), nullable=True),
    sa.Column("decision_checksum", sa.String(64), nullable=True),
)


def upgrade() -> None:
    for column in VERSION_COLUMNS:
        op.add_column(VERSIONS, column)
    for column in RIGHTS_COLUMNS:
        op.add_column(RIGHTS, column)
    op.create_check_constraint(
        "ck_knowledge_version_content_mode",
        VERSIONS,
        "content_mode IN ('METADATA_ONLY', 'ORIGINAL_SYNTHETIC')",
    )
    op.create_check_constraint(
        "ck_knowledge_metadata_only_no_file",
        VERSIONS,
        "content_mode <> 'METADATA_ONLY' OR "
        "(storage_key IS NULL AND storage_bucket IS NULL AND sha256 IS NULL "
        "AND file_size IS NULL AND mime_type IS NULL AND original_filename IS NULL)",
    )
    op.execute("""
    CREATE FUNCTION prufai_source_version_sealed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        IF OLD.metadata_checksum IS NOT NULL THEN
          RAISE EXCEPTION 'SOURCE_VERSION_HISTORY_IMMUTABLE';
        END IF;
        RETURN OLD;
      END IF;
      IF OLD.metadata_checksum IS NOT NULL AND (
        ROW(OLD.id, OLD.source_id, OLD.version_label, OLD.content_mode, OLD.metadata_checksum,
            OLD.publication_date, OLD.effective_from, OLD.effective_until, OLD.source_url,
            OLD.retrieved_at, OLD.created_at, OLD.created_by_user_id,
            OLD.supersedes_document_version_id) IS DISTINCT FROM
        ROW(NEW.id, NEW.source_id, NEW.version_label, NEW.content_mode, NEW.metadata_checksum,
            NEW.publication_date, NEW.effective_from, NEW.effective_until, NEW.source_url,
            NEW.retrieved_at, NEW.created_at, NEW.created_by_user_id,
            NEW.supersedes_document_version_id)
        OR (OLD.status = 'RETIRED' AND NEW.status <> 'RETIRED')
        OR (OLD.status = 'APPROVED' AND NEW.status NOT IN ('APPROVED','SUPERSEDED','RETIRED'))
        OR (OLD.status = 'SUPERSEDED' AND NEW.status NOT IN ('SUPERSEDED','RETIRED'))
        OR (OLD.reviewed_at IS NOT NULL AND (
          ROW(OLD.reviewed_at, OLD.reviewed_by_user_id, OLD.approved_at,
              OLD.approved_by_user_id, OLD.rejection_reason) IS DISTINCT FROM
          ROW(NEW.reviewed_at, NEW.reviewed_by_user_id, NEW.approved_at,
              NEW.approved_by_user_id, NEW.rejection_reason)
        ))
        OR (OLD.page_count IS NOT NULL AND OLD.page_count IS DISTINCT FROM NEW.page_count)
      ) THEN
        RAISE EXCEPTION 'SOURCE_VERSION_HISTORY_IMMUTABLE';
      END IF;
      IF OLD.storage_key IS NOT NULL AND (
        ROW(OLD.storage_key, OLD.storage_bucket, OLD.sha256, OLD.file_size,
            OLD.original_filename, OLD.mime_type) IS DISTINCT FROM
        ROW(NEW.storage_key, NEW.storage_bucket, NEW.sha256, NEW.file_size,
            NEW.original_filename, NEW.mime_type)
      ) THEN
        RAISE EXCEPTION 'SOURCE_OBJECT_IDENTITY_IMMUTABLE';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER source_version_sealed BEFORE UPDATE OR DELETE ON knowledge_document_versions
      FOR EACH ROW EXECUTE FUNCTION prufai_source_version_sealed();
    """)
    op.execute("""
    CREATE FUNCTION prufai_source_rights_sealed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.decision_checksum IS NOT NULL THEN
        RAISE EXCEPTION 'SOURCE_RIGHTS_DECISION_IMMUTABLE';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER source_rights_sealed BEFORE UPDATE OR DELETE ON knowledge_legal_metadata
      FOR EACH ROW EXECUTE FUNCTION prufai_source_rights_sealed();
    """)
    op.execute("""
    CREATE FUNCTION prufai_source_page_sealed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM knowledge_document_versions
        WHERE id = OLD.document_version_id AND metadata_checksum IS NOT NULL
      ) THEN
        RAISE EXCEPTION 'SOURCE_PAGE_HISTORY_IMMUTABLE';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER source_page_sealed BEFORE UPDATE OR DELETE ON knowledge_document_pages
      FOR EACH ROW EXECUTE FUNCTION prufai_source_page_sealed();
    """)
    op.execute("""
    CREATE FUNCTION prufai_source_rights_audit_sealed() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF starts_with(OLD.event_type, 'standards.source_rights.') THEN
        RAISE EXCEPTION 'SOURCE_RIGHTS_AUDIT_IMMUTABLE';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER source_rights_audit_sealed BEFORE UPDATE OR DELETE ON audit_events
      FOR EACH ROW EXECUTE FUNCTION prufai_source_rights_audit_sealed();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER source_rights_audit_sealed ON audit_events")
    op.execute("DROP FUNCTION prufai_source_rights_audit_sealed()")
    op.execute("DROP TRIGGER source_page_sealed ON knowledge_document_pages")
    op.execute("DROP FUNCTION prufai_source_page_sealed()")
    op.execute("DROP TRIGGER source_rights_sealed ON knowledge_legal_metadata")
    op.execute("DROP FUNCTION prufai_source_rights_sealed()")
    op.execute("DROP TRIGGER source_version_sealed ON knowledge_document_versions")
    op.execute("DROP FUNCTION prufai_source_version_sealed()")
    op.drop_constraint("ck_knowledge_version_content_mode", VERSIONS, type_="check")
    op.drop_constraint("ck_knowledge_metadata_only_no_file", VERSIONS, type_="check")
    for column in reversed(RIGHTS_COLUMNS):
        op.drop_column(RIGHTS, column.name)
    for column in reversed(VERSION_COLUMNS):
        op.drop_column(VERSIONS, column.name)
