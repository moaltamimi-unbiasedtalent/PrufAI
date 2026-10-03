import os

import psycopg
import pytest

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for correction-chain integration",
)


def test_result_correction_chain_schema_is_migration_backed() -> None:
    configured_url = os.environ.get("POSTGRES_INTEGRATION_URL")
    if not configured_url:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    url = configured_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as connection:
        rows = connection.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN (
                'result_corrections',
                'result_correction_objective_bindings',
                'result_appeals',
                'result_appeal_versions',
                'scoring_runs',
                'result_versions'
              )
            """
        ).fetchall()
        assert {row[0] for row in rows} == {
            "result_corrections",
            "result_correction_objective_bindings",
            "result_appeals",
            "result_appeal_versions",
            "scoring_runs",
            "result_versions",
        }
        columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'result_corrections'
                """
            ).fetchall()
        }
        assert {
            "appeal_id",
            "appeal_version_id",
            "source_result_version_id",
            "correction_type",
            "status",
            "operation_id",
            "new_component_version_id",
            "new_transcription_version_id",
            "new_scoring_run_id",
            "new_result_version_id",
        }.issubset(columns)
