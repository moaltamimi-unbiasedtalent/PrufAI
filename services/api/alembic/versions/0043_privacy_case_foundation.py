"""Persist scoped privacy cases and immutable discovery receipts."""

import sqlalchemy as sa

from alembic import op

revision = "0043_privacy_case_foundation"
down_revision = "0042_initial_review_authority"
branch_labels = None
depends_on = None

TABLES = (
    "privacy_grants",
    "privacy_cases",
    "privacy_scopes",
    "privacy_policy_drafts",
    "privacy_hold_reviews",
    "privacy_previews",
)


def upgrade() -> None:
    op.create_table(
        "privacy_grants",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("holder_membership_id", sa.Uuid(), nullable=False),
        sa.Column("subject_membership_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("authority_receipt", sa.Uuid(), nullable=False),
        sa.Column("issued_by", sa.Uuid(), nullable=False),
        sa.Column("maturity", sa.String(length=16), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("expires_at > starts_at", name="ck_privacy_grant_window"),
        sa.CheckConstraint(
            "holder_membership_id <> subject_membership_id", name="ck_privacy_grant_separation"
        ),
        sa.ForeignKeyConstraint(
            ["holder_membership_id"],
            ["organisation_memberships.id"],
        ),
        sa.ForeignKeyConstraint(
            ["issued_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["subject_membership_id"],
            ["organisation_memberships.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_privacy_grants_holder",
        "privacy_grants",
        ["organisation_id", "holder_membership_id"],
        unique=False,
    )
    op.create_table(
        "privacy_cases",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("subject_membership_id", sa.Uuid(), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False),
        sa.Column("requester_id", sa.Uuid(), nullable=False),
        sa.Column("request_type", sa.String(length=24), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("request_receipt", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["requester_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["subject_membership_id"],
            ["organisation_memberships.id"],
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_index(
        "ix_privacy_case_scope",
        "privacy_cases",
        ["organisation_id", "subject_membership_id", "id"],
        unique=False,
    )
    op.create_table(
        "privacy_scopes",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("verification_receipt", sa.Uuid(), nullable=False),
        sa.Column("controller_context", sa.String(length=24), nullable=False),
        sa.Column("context_receipt", sa.Uuid(), nullable=True),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["privacy_cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_table(
        "privacy_policy_drafts",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("terms", sa.JSON(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["organisations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
        sa.UniqueConstraint(
            "organisation_id", "purpose", "category", "version", name="uq_privacy_policy_version"
        ),
    )
    op.create_table(
        "privacy_hold_reviews",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["privacy_cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["privacy_scopes.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "version", name="uq_privacy_hold_version"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_table(
        "privacy_previews",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("grant_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_checksum", sa.String(length=64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["privacy_cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["privacy_grants.id"],
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["privacy_scopes.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_index(
        "ix_privacy_preview_case", "privacy_previews", ["case_id", "created_at"], unique=False
    )

    op.execute("""
    CREATE FUNCTION prufai_privacy_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME = 'audit_events' THEN
        IF OLD.event_type NOT LIKE 'privacy.%' THEN
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          IF NEW.event_type NOT LIKE 'privacy.%' THEN RETURN NEW; END IF;
        END IF;
      ELSIF TG_TABLE_NAME = 'privacy_grants' AND TG_OP = 'UPDATE' THEN
        IF OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL AND
           to_jsonb(OLD) - 'revoked_at' = to_jsonb(NEW) - 'revoked_at' THEN
          RETURN NEW;
        END IF;
      END IF;
      RAISE EXCEPTION 'PRIVACY_EVIDENCE_IMMUTABLE';
    END $$;
    """)
    for table in (*TABLES, "audit_events"):
        op.execute(
            f"CREATE TRIGGER privacy_evidence_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prufai_privacy_immutable()"
        )


def downgrade() -> None:
    for table in (*TABLES, "audit_events"):
        op.execute(f"DROP TRIGGER privacy_evidence_immutable ON {table}")
    op.execute("DROP FUNCTION prufai_privacy_immutable()")
    for table in reversed(TABLES):
        op.drop_table(table)
