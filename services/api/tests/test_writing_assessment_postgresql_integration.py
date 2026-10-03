import os

import psycopg
import pytest

from tests.alembic_helpers import repository_alembic_head

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for writing assessment integration",
)


def test_writing_assessment_schema_is_migration_backed() -> None:
    configured_url = os.environ.get("POSTGRES_INTEGRATION_URL")
    if not configured_url:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    with psycopg.connect(
        configured_url.replace("postgresql+psycopg://", "postgresql://")
    ) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        assert {
            "assessment_rubrics",
            "rubric_versions",
            "rubric_evidence_bindings",
            "subjective_assessments",
            "subjective_assessment_versions",
            "subjective_criterion_assessments",
        } <= tables
        constraints = {
            row[0]
            for row in connection.execute(
                """
                SELECT constraint_name FROM information_schema.table_constraints
                WHERE table_schema = 'public' AND table_name IN
                ('assessment_rubrics', 'rubric_versions', 'subjective_assessments',
                 'subjective_assessment_versions', 'subjective_criterion_assessments')
                """
            )
        }
        assert {
            "uq_assessment_rubric_scope_code",
            "uq_rubric_version_label",
            "uq_subjective_assessment_item",
            "uq_subjective_assessment_version",
            "uq_subjective_criterion_version",
        } <= constraints
        numeric = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name IN (
                    'subjective_assessment_versions', 'subjective_criterion_assessments'
                )
                AND data_type = 'numeric'
                """
            )
        }
        assert {"proposed_score", "approved_score", "proposed_points", "approved_points"} <= numeric
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == repository_alembic_head()
        )
