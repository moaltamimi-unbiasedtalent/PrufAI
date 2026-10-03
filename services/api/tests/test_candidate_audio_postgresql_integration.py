import os

import psycopg
import pytest

from tests.alembic_helpers import repository_alembic_head

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for Candidate Audio integration",
)


def test_candidate_audio_schema_and_migration_head() -> None:
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
            "candidate_audio_recording_sessions",
            "candidate_media_assets",
            "candidate_response_media_bindings",
            "candidate_response_revisions",
        } <= tables
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == repository_alembic_head()
        )
