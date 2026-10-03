import os

import psycopg
import pytest

from tests.alembic_helpers import repository_alembic_head

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for speaking assessment integration",
)


def test_speaking_assessment_provenance_is_migration_backed() -> None:
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
                WHERE table_schema = 'public' AND table_name = 'subjective_assessment_versions'
                """
            )
        }
        assert {
            "candidate_media_asset_id",
            "audio_sha256",
            "speech_transcription_version_id",
            "transcript_checksum",
            "evidence_mode_summary",
            "ai_proposal",
            "human_approved_values",
            "consistency_summary",
        } <= columns
