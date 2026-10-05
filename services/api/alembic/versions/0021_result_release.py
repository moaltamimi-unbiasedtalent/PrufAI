"""Add immutable governed result releases, statements, and certification controls."""

import sqlalchemy as sa

from alembic import op

revision = "0021_result_release"
down_revision = "0020_speaking_assessment"
branch_labels = None
depends_on = None


def _fk(table: str, ondelete: str = "RESTRICT") -> sa.ForeignKey:
    return sa.ForeignKey(f"{table}.id", ondelete=ondelete)


def upgrade() -> None:
    op.create_table(
        "result_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organisation_id", sa.Uuid(), _fk("organisations"), nullable=False),
        sa.Column("candidate_user_id", sa.Uuid(), _fk("users"), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), _fk("candidate_exam_attempts"), nullable=False),
        sa.Column("assessment_result_id", sa.Uuid(), _fk("assessment_results"), nullable=False),
        sa.Column("result_version_id", sa.Uuid(), _fk("result_versions"), nullable=False),
        sa.Column("scoring_run_id", sa.Uuid(), _fk("scoring_runs"), nullable=False),
        sa.Column(
            "standard_pack_version_id", sa.Uuid(), _fk("standard_pack_versions"), nullable=False
        ),
        sa.Column("released_by_user_id", sa.Uuid(), _fk("users"), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("operation_id", sa.String(120), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("supersedes_release_id", sa.Uuid(), _fk("result_releases", "SET NULL")),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "result_version_id", "operation_id", name="uq_result_release_operation"
        ),
    )
    op.create_index(
        "ix_result_releases_candidate", "result_releases", ["candidate_user_id", "organisation_id"]
    )
    op.create_table(
        "statements_of_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("release_id", sa.Uuid(), _fk("result_releases"), nullable=False),
        sa.Column("result_version_id", sa.Uuid(), _fk("result_versions"), nullable=False),
        sa.Column("scoring_run_id", sa.Uuid(), _fk("scoring_runs"), nullable=False),
        sa.Column(
            "standard_pack_version_id", sa.Uuid(), _fk("standard_pack_versions"), nullable=False
        ),
        sa.Column("organisation_id", sa.Uuid(), _fk("organisations"), nullable=False),
        sa.Column("candidate_user_id", sa.Uuid(), _fk("users"), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), _fk("candidate_exam_attempts"), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("statement_version", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("operation_id", sa.String(120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("release_id", "operation_id", name="uq_statement_release_operation"),
    )
    op.create_table(
        "certification_authorizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organisation_id", sa.Uuid(), _fk("organisations"), nullable=False),
        sa.Column("assessment_scope", sa.String(120), nullable=False),
        sa.Column("provider_scope", sa.String(120), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("evidence_reference", sa.String(240), nullable=False),
        sa.Column("approved_by_user_id", sa.Uuid(), _fk("users"), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_certification_authorizations_scope",
        "certification_authorizations",
        ["organisation_id", "status"],
    )
    op.create_table(
        "certificate_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("release_id", sa.Uuid(), _fk("result_releases"), nullable=False),
        sa.Column("result_version_id", sa.Uuid(), _fk("result_versions"), nullable=False),
        sa.Column(
            "authorization_id", sa.Uuid(), _fk("certification_authorizations"), nullable=False
        ),
        sa.Column("organisation_id", sa.Uuid(), _fk("organisations"), nullable=False),
        sa.Column("candidate_user_id", sa.Uuid(), _fk("users"), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("serial", sa.String(160), nullable=False, unique=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("operation_id", sa.String(120), nullable=False),
        sa.UniqueConstraint("release_id", "operation_id", name="uq_certificate_release_operation"),
    )


def downgrade() -> None:
    op.drop_table("certificate_records")
    op.drop_index(
        "ix_certification_authorizations_scope", table_name="certification_authorizations"
    )
    op.drop_table("certification_authorizations")
    op.drop_table("statements_of_results")
    op.drop_index("ix_result_releases_candidate", table_name="result_releases")
    op.drop_table("result_releases")
