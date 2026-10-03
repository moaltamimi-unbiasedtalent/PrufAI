import os

import psycopg
import pytest

from tests.alembic_helpers import repository_alembic_head

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for scoring integration",
)


def test_scoring_results_schema_is_migration_backed() -> None:
    configured_url = os.environ.get("POSTGRES_INTEGRATION_URL")
    if not configured_url:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    url = configured_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        assert {
            "scoring_runs",
            "scoring_response_snapshots",
            "item_scores",
            "scoring_components",
            "assessment_results",
            "result_versions",
        } <= tables
        constraints = {
            row[0]
            for row in connection.execute(
                """
                SELECT constraint_name FROM information_schema.table_constraints
                WHERE table_schema = 'public' AND table_name IN
                ('scoring_runs', 'scoring_response_snapshots', 'item_scores',
                 'assessment_results', 'result_versions')
                """
            )
        }
        assert {
            "uq_scoring_run_idempotency",
            "uq_scoring_snapshot_run",
            "uq_item_score_run_binding",
            "uq_result_attempt",
            "uq_result_version_number",
        } <= constraints
        numeric_columns = {
            (row[0], row[1], row[2])
            for row in connection.execute(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name IN
                ('item_scores', 'scoring_components', 'result_versions')
                AND column_name IN
                ('awarded_points', 'maximum_points', 'raw_awarded_points',
                 'raw_maximum_points', 'transformed_value')
                """
            )
        }
        assert numeric_columns
        assert all(data_type == "numeric" for _, _, data_type in numeric_columns)
        columns = {
            (row[0], row[1])
            for row in connection.execute(
                """
                SELECT table_name, column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name IN
                ('scoring_runs', 'assessment_results', 'result_versions')
                """
            )
        }
        assert ("scoring_runs", "organisation_id") in columns
        assert ("assessment_results", "organisation_id") in columns
        assert ("result_versions", "supersedes_version_id") in columns
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == repository_alembic_head()
        )
