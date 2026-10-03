from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect


@pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL"),
    reason="PostgreSQL AI integration service is configured in CI only",
)
def test_ai_orchestration_schema_is_migrated() -> None:
    engine = create_engine(os.environ["POSTGRES_INTEGRATION_URL"])
    tables = set(inspect(engine).get_table_names())
    assert {
        "ai_model_profiles",
        "prompt_definitions",
        "prompt_versions",
        "agent_definitions",
        "agent_versions",
        "ai_workflow_definitions",
        "ai_workflow_versions",
        "ai_runs",
        "ai_run_steps",
        "ai_workflow_checkpoints",
        "human_review_requests",
    } <= tables
