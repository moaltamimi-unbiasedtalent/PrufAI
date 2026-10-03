import os

import psycopg
import pytest

from tests.alembic_helpers import repository_alembic_head

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL"),
    reason="POSTGRES_INTEGRATION_URL is required for Exam Studio integration",
)


def test_exam_studio_schema_is_migration_backed() -> None:
    url = os.environ["POSTGRES_INTEGRATION_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        assert {
            "exams",
            "exam_versions",
            "exam_version_modules",
            "exam_version_sections",
            "exam_version_tasks",
            "exam_version_item_bindings",
        } <= tables
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == repository_alembic_head()
        )
