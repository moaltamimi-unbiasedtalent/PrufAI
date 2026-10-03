import os

import psycopg
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL"),
    reason="POSTGRES_INTEGRATION_URL is required for migration-backed integration tests",
)


def test_memory_schema_is_exercised_by_ci_migration() -> None:
    """Inspect the schema produced by Alembic; never create tables from metadata."""
    url = os.environ["POSTGRES_INTEGRATION_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        assert {
            "long_term_memories",
            "long_term_memory_versions",
            "memory_evidence_bindings",
            "ai_run_memory_references",
        } <= tables
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0011_governed_memory"
        )
