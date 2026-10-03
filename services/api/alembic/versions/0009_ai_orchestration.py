"""Add governed AI orchestration, provenance and human review records."""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0009_ai_orchestration"
down_revision: Union[str, None] = "0008_rules_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _common(name: str) -> None:
    op.create_table(name, sa.Column("id", sa.Uuid(), primary_key=True, nullable=False))


def upgrade() -> None:
    enum = lambda n: sa.String(length=40)  # noqa: E731
    op.create_table(
        "ai_model_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(160), nullable=False, unique=True),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model_name", sa.String(160), nullable=False),
        sa.Column("purpose", sa.String(120), nullable=False),
        sa.Column("status", enum("status"), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("supports_structured_output", sa.Boolean(), nullable=False),
        sa.Column("context_window", sa.Integer()),
        sa.Column("max_output_tokens", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by", sa.Uuid()),
    )
    op.create_table(
        "prompt_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(160), unique=True, nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("purpose", sa.String(240), nullable=False),
        sa.Column("status", enum("status"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "prompt_definition_id",
            sa.Uuid(),
            sa.ForeignKey("prompt_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(80), nullable=False),
        sa.Column("system_template", sa.Text(), nullable=False),
        sa.Column("instruction_template", sa.Text()),
        sa.Column("input_schema_version", sa.String(40), nullable=False),
        sa.Column("output_schema_version", sa.String(40), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("status", enum("status"), nullable=False),
        sa.Column("checksum", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.Uuid()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by", sa.Uuid()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("published_by", sa.Uuid()),
        sa.UniqueConstraint("prompt_definition_id", "version_label", name="uq_prompt_version"),
    )
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(160), unique=True, nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("purpose", sa.String(240), nullable=False),
        sa.Column("status", enum("status"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "agent_definition_id",
            sa.Uuid(),
            sa.ForeignKey("agent_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(80), nullable=False),
        sa.Column(
            "model_profile_id",
            sa.Uuid(),
            sa.ForeignKey("ai_model_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "prompt_version_id",
            sa.Uuid(),
            sa.ForeignKey("prompt_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tool_policy", sa.JSON(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("status", enum("status"), nullable=False),
        sa.Column("checksum", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid()),
        sa.UniqueConstraint("agent_definition_id", "version_label", name="uq_agent_version"),
    )
    op.create_table(
        "ai_workflow_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(160), unique=True, nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("purpose", sa.String(240), nullable=False),
        sa.Column("status", enum("status"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_workflow_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workflow_definition_id",
            sa.Uuid(),
            sa.ForeignKey("ai_workflow_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_label", sa.String(80), nullable=False),
        sa.Column("graph_schema_version", sa.String(40), nullable=False),
        sa.Column("graph_definition", sa.JSON(), nullable=False),
        sa.Column("status", enum("status"), nullable=False),
        sa.Column("checksum", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid()),
        sa.UniqueConstraint("workflow_definition_id", "version_label", name="uq_workflow_version"),
    )
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "workflow_version_id",
            sa.Uuid(),
            sa.ForeignKey("ai_workflow_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "agent_version_id", sa.Uuid(), sa.ForeignKey("agent_versions.id", ondelete="RESTRICT")
        ),
        sa.Column("status", enum("status"), nullable=False),
        sa.Column("request_id", sa.String(120), nullable=False),
        sa.Column("organisation_id", sa.Uuid()),
        sa.Column("standard_pack_version_id", sa.Uuid()),
        sa.Column("standard_pack_checksum", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("initiated_by_user_id", sa.Uuid()),
        sa.Column("failure_category", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_run_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(120), nullable=False),
        sa.Column("agent_version_id", sa.Uuid()),
        sa.Column("model_profile_id", sa.Uuid()),
        sa.Column("prompt_version_id", sa.Uuid()),
        sa.Column("input_checksum", sa.String(64)),
        sa.Column("output_checksum", sa.String(64)),
        sa.Column("status", enum("status"), nullable=False),
        sa.Column("token_usage", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("provider_request_id", sa.String(160)),
        sa.Column("error_category", sa.String(120)),
        sa.Column("retrieval_ids", sa.JSON(), nullable=False),
        sa.Column("rule_evaluation_ids", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "human_review_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("review_type", sa.String(120), nullable=False),
        sa.Column("entity_type", sa.String(120), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("ai_run_id", sa.Uuid(), sa.ForeignKey("ai_runs.id", ondelete="SET NULL")),
        sa.Column("status", enum("status"), nullable=False),
        sa.Column("requested_by", sa.Uuid()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_to", sa.Uuid()),
        sa.Column("decided_by", sa.Uuid()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision", sa.String(80)),
        sa.Column("reason", sa.Text()),
        sa.Column("reviewed_checksum", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "human_review_requests",
        "ai_run_steps",
        "ai_runs",
        "ai_workflow_versions",
        "ai_workflow_definitions",
        "agent_versions",
        "agent_definitions",
        "prompt_versions",
        "prompt_definitions",
        "ai_model_profiles",
    ):
        op.drop_table(table)
