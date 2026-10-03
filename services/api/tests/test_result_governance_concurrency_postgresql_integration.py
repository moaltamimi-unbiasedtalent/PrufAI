import os
import uuid

import psycopg
import pytest

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for concurrency integration",
)


def test_postgres_row_lock_serializes_consequential_transition() -> None:
    configured_url = os.environ.get("POSTGRES_INTEGRATION_URL")
    if not configured_url:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    url = configured_url.replace("postgresql+psycopg://", "postgresql://")
    table = f"prufai_governance_lock_probe_{uuid.uuid4().hex}"
    first = psycopg.connect(url)
    second = psycopg.connect(url)
    try:
        first.execute(f'CREATE TABLE "{table}" (id integer PRIMARY KEY, checksum text NOT NULL)')
        first.execute(f'INSERT INTO "{table}" (id, checksum) VALUES (1, %s)', ("v1",))
        first.commit()
        first.execute(f'SELECT checksum FROM "{table}" WHERE id = 1 FOR UPDATE')
        with pytest.raises(psycopg.errors.LockNotAvailable):
            second.execute(f'SELECT checksum FROM "{table}" WHERE id = 1 FOR UPDATE NOWAIT')
        first.rollback()
        second.rollback()
        second.execute(f'SELECT checksum FROM "{table}" WHERE id = 1 FOR UPDATE')
        assert second.fetchone()[0] == "v1"
        second.rollback()
        first.execute(f'DROP TABLE "{table}"')
        first.commit()
    finally:
        first.close()
        second.close()
