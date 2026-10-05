import os

import psycopg
import pytest

from tests.alembic_helpers import repository_alembic_head

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for result-governance integration",
)


def test_result_governance_schema_is_migration_backed() -> None:
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
            "result_appeals",
            "result_appeal_versions",
            "appeal_information_entries",
            "result_moderation_cases",
            "result_corrections",
            "assessment_results",
            "result_versions",
            "scoring_runs",
        } <= tables
        constraints = {
            (row[0], row[1])
            for row in connection.execute(
                """
                SELECT table_name, constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'public' AND table_name IN (
                    'result_appeals',
                    'result_appeal_versions',
                    'result_moderation_cases',
                    'result_corrections'
                )
                """
            )
        }
        assert {
            ("result_appeals", "uq_result_appeal_operation"),
            ("result_appeal_versions", "uq_result_appeal_version_number"),
            ("result_appeal_versions", "uq_result_appeal_version_operation"),
            ("result_moderation_cases", "uq_result_moderation_operation"),
            ("result_corrections", "uq_result_correction_operation"),
        } <= constraints
        columns = {
            (row[0], row[1])
            for row in connection.execute(
                """
                SELECT table_name, column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name IN
                ('result_appeals', 'result_appeal_versions')
                """
            )
        }
        assert ("result_appeals", "challenged_result_version_id") in columns
        assert ("result_appeal_versions", "evidence_bundle_checksum") in columns
        correction_columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'result_corrections'
                """
            )
        }
        assert {
            "appeal_version_id",
            "source_result_version_id",
            "correction_type",
            "status",
            "new_scoring_run_id",
            "new_result_version_id",
        } <= correction_columns
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == repository_alembic_head()
        )
