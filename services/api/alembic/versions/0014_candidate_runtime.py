"""Add governed candidate exam runtime records."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0014_candidate_runtime"
down_revision: Union[str, None] = "0013_exam_studio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    enum = lambda: sa.String(40)  # noqa: E731
    op.create_table(
        "candidate_exam_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organisation_id",
            sa.Uuid(),
            sa.ForeignKey("organisations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "exam_version_id",
            sa.Uuid(),
            sa.ForeignKey("exam_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", enum(), nullable=False),
        sa.Column("available_from", sa.DateTime(timezone=True)),
        sa.Column("available_until", sa.DateTime(timezone=True)),
        sa.Column("maximum_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_by_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("maximum_attempts > 0", name="ck_candidate_assignment_attempts"),
        sa.CheckConstraint(
            "available_until IS NULL OR available_from IS NULL OR available_until > available_from",
            name="ck_candidate_assignment_window",
        ),
    )
    op.create_index(
        "ix_candidate_assignment_candidate_status",
        "candidate_exam_assignments",
        ["candidate_user_id", "status"],
    )
    op.create_index(
        "ix_candidate_assignment_org_status",
        "candidate_exam_assignments",
        ["organisation_id", "status"],
    )
    op.create_table(
        "candidate_exam_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "assignment_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_exam_assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "organisation_id",
            sa.Uuid(),
            sa.ForeignKey("organisations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "exam_version_id",
            sa.Uuid(),
            sa.ForeignKey("exam_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", enum(), nullable=False),
        sa.Column("delivery_manifest_checksum", sa.String(64), nullable=False),
        sa.Column("runtime_plan_checksum", sa.String(64), nullable=False),
        sa.Column("runtime_plan", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_number > 0", name="ck_candidate_attempt_number"),
        sa.UniqueConstraint("assignment_id", "attempt_number", name="uq_candidate_attempt_number"),
    )
    op.create_index(
        "ix_candidate_attempt_status_deadline", "candidate_exam_attempts", ["status", "deadline_at"]
    )
    op.create_index(
        "uq_candidate_active_attempt",
        "candidate_exam_attempts",
        ["assignment_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('READY', 'IN_PROGRESS')"),
    )
    op.create_table(
        "candidate_exam_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_exam_attempts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("current_module_id", sa.Uuid()),
        sa.Column("current_section_id", sa.Uuid()),
        sa.Column("current_task_id", sa.Uuid()),
        sa.Column("current_binding_id", sa.Uuid()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_candidate_session_last_seen", "candidate_exam_sessions", ["last_seen_at"])
    op.create_table(
        "candidate_responses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_exam_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "exam_version_item_binding_id",
            sa.Uuid(),
            sa.ForeignKey("exam_version_item_bindings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("response_schema_version", sa.String(40), nullable=False),
        sa.Column("current_payload", sa.JSON(), nullable=False),
        sa.Column("first_saved_at", sa.DateTime(timezone=True)),
        sa.Column("last_saved_at", sa.DateTime(timezone=True)),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "attempt_id", "exam_version_item_binding_id", name="uq_candidate_response_binding"
        ),
    )
    op.create_index("ix_candidate_response_attempt", "candidate_responses", ["attempt_id"])
    op.create_table(
        "candidate_response_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "candidate_response_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_responses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_exam_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("client_operation_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_checksum", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_observed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("revision_number > 0", name="ck_candidate_revision_number"),
        sa.UniqueConstraint(
            "candidate_response_id", "revision_number", name="uq_candidate_response_revision"
        ),
        sa.UniqueConstraint("attempt_id", "client_operation_id", name="uq_candidate_operation"),
    )


def downgrade() -> None:
    op.drop_table("candidate_response_revisions")
    op.drop_index("ix_candidate_response_attempt", table_name="candidate_responses")
    op.drop_table("candidate_responses")
    op.drop_index("ix_candidate_session_last_seen", table_name="candidate_exam_sessions")
    op.drop_table("candidate_exam_sessions")
    op.drop_index("uq_candidate_active_attempt", table_name="candidate_exam_attempts")
    op.drop_index("ix_candidate_attempt_status_deadline", table_name="candidate_exam_attempts")
    op.drop_table("candidate_exam_attempts")
    op.drop_index("ix_candidate_assignment_org_status", table_name="candidate_exam_assignments")
    op.drop_index(
        "ix_candidate_assignment_candidate_status", table_name="candidate_exam_assignments"
    )
    op.drop_table("candidate_exam_assignments")
