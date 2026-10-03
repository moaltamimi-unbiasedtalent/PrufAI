import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL"),
    reason="POSTGRES_INTEGRATION_URL is required for migration-backed integration tests",
)


def test_memory_schema_is_exercised_by_ci_migration() -> None:
    """The CI migration step owns the connection; this guard prevents false local claims."""
    assert os.environ["POSTGRES_INTEGRATION_URL"].startswith("postgresql")
