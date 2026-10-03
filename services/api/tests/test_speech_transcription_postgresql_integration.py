import os

import psycopg
import pytest

from tests.alembic_helpers import repository_alembic_head

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for speech transcription integration",
)


def test_speech_transcription_schema_is_migration_backed() -> None:
    configured_url = os.environ.get("POSTGRES_INTEGRATION_URL")
    if not configured_url:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    url = configured_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as connection:
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == repository_alembic_head()
        )
        columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'speech_transcriptions'
                """
            )
        }
        assert {"asset_id", "operation_id", "failure_category", "retry_count"} <= columns
        version_columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'speech_transcription_versions'
                """
            )
        }
        assert {
            "status",
            "provider",
            "model_name",
            "adapter_version",
            "configuration_checksum",
            "audio_sha256",
        } <= version_columns
