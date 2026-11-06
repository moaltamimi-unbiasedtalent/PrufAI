"""Persist sealed, temporary source review grants and replay protection."""

import sqlalchemy as sa

from alembic import op

revision = "0041_scoped_source_review"
down_revision = "0040_lid_editorial_review"
branch_labels = None
depends_on = None

GRANTS = "scoped_source_review_grants"
OPERATIONS = "scoped_source_review_operations"


def _reference(name: str, table: str) -> sa.Column:
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey(f"{table}.id", ondelete="RESTRICT"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        GRANTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        _reference("reviewer_user_id", "users"),
        _reference("organisation_id", "organisations"),
        _reference("authorized_by", "users"),
        sa.Column("creation_operation_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("purpose", sa.String(240), nullable=False),
        sa.Column("business_approval_reference", sa.String(71), unique=True, nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("scope_checksum", sa.String(64), nullable=False),
        sa.Column("resources", sa.JSON(), nullable=False),
        sa.CheckConstraint("expires_at > starts_at", name="ck_scoped_review_window"),
        sa.CheckConstraint(
            "reviewer_user_id <> authorized_by", name="ck_scoped_review_distinct_issuer"
        ),
        sa.CheckConstraint(
            "status IN ('APPROVED', 'ACTIVE', 'SUSPENDED', 'REVOKED', 'EXPIRED')",
            name="ck_scoped_review_grant_status",
        ),
    )
    op.create_index(
        "ix_scoped_review_reviewer_org", GRANTS, ["reviewer_user_id", "organisation_id"]
    )
    op.create_table(
        OPERATIONS,
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        _reference("grant_id", GRANTS),
        _reference("actor_id", "users"),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("request_checksum", sa.String(64), nullable=False),
    )
    op.execute("""
    CREATE FUNCTION prufai_scoped_review_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME = 'scoped_source_review_grants' AND TG_OP = 'UPDATE' THEN
        IF to_jsonb(OLD) - 'status' = to_jsonb(NEW) - 'status' AND
          ((OLD.status = 'APPROVED' AND NEW.status IN ('ACTIVE','SUSPENDED','REVOKED','EXPIRED')) OR
           (OLD.status = 'ACTIVE' AND NEW.status IN ('SUSPENDED','REVOKED','EXPIRED')) OR
           (OLD.status = 'SUSPENDED' AND NEW.status = 'REVOKED')) THEN
          RETURN NEW;
        END IF;
      ELSIF TG_TABLE_NAME = 'audit_events' THEN
        IF OLD.event_type NOT LIKE 'standards.scoped_review.%' AND
           (OLD.metadata->>'scoped_grant_id') IS NULL THEN
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          IF NEW.event_type NOT LIKE 'standards.scoped_review.%' AND
             (NEW.metadata->>'scoped_grant_id') IS NULL THEN
            RETURN NEW;
          END IF;
        END IF;
      END IF;
      RAISE EXCEPTION 'SCOPED_SOURCE_REVIEW_HISTORY_IMMUTABLE';
    END $$;
    """)
    for table in (GRANTS, OPERATIONS, "audit_events"):
        op.execute(
            f"CREATE TRIGGER scoped_review_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prufai_scoped_review_immutable()"
        )


def downgrade() -> None:
    for table in (GRANTS, OPERATIONS, "audit_events"):
        op.execute(f"DROP TRIGGER scoped_review_immutable ON {table}")
    op.execute("DROP FUNCTION prufai_scoped_review_immutable()")
    op.drop_table(OPERATIONS)
    op.drop_table(GRANTS)
