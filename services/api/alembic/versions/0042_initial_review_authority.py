"""Persist a single initial review authority and seal recovery evidence."""

import sqlalchemy as sa

from alembic import op

revision = "0042_initial_review_authority"
down_revision = "0041_scoped_source_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "initial_review_authority",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("envelope", sa.JSON(), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("established_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("end_action", sa.String(10)),
        sa.Column("end_digest", sa.String(64)),
        sa.CheckConstraint("id = 1", name="ck_initial_review_authority_singleton"),
        sa.CheckConstraint(
            "(ended_at IS NULL AND end_action IS NULL AND end_digest IS NULL) OR "
            "(ended_at IS NOT NULL AND end_action IS NOT NULL AND end_digest IS NOT NULL "
            "AND end_action IN ('SUSPEND', 'REVOKE'))",
            name="ck_initial_review_authority_end",
        ),
    )
    op.execute("""
    CREATE FUNCTION prufai_initial_review_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME = 'audit_events' THEN
        IF OLD.event_type NOT LIKE 'standards.initial_review.%' THEN
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          IF NEW.event_type NOT LIKE 'standards.initial_review.%' THEN RETURN NEW; END IF;
        END IF;
      ELSIF TG_OP = 'UPDATE' THEN
        IF OLD.ended_at IS NULL AND NEW.ended_at IS NOT NULL AND
          to_jsonb(OLD) - ARRAY['ended_at','end_action','end_digest'] =
          to_jsonb(NEW) - ARRAY['ended_at','end_action','end_digest'] THEN
          RETURN NEW;
        END IF;
      END IF;
      RAISE EXCEPTION 'INITIAL_REVIEW_AUTHORITY_IMMUTABLE';
    END $$;
    CREATE TRIGGER initial_review_immutable BEFORE UPDATE OR DELETE ON initial_review_authority
      FOR EACH ROW EXECUTE FUNCTION prufai_initial_review_immutable();
    CREATE TRIGGER initial_review_immutable BEFORE UPDATE OR DELETE ON audit_events
      FOR EACH ROW EXECUTE FUNCTION prufai_initial_review_immutable();
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER initial_review_immutable ON audit_events;
    DROP TRIGGER initial_review_immutable ON initial_review_authority;
    DROP FUNCTION prufai_initial_review_immutable();
    """)
    op.drop_table("initial_review_authority")
