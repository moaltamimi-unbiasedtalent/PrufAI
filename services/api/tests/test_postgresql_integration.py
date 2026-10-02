import os

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL"),
    reason="PostgreSQL integration service is configured in CI only",
)
def test_postgresql_schema_is_reachable() -> None:
    engine = create_engine(os.environ["POSTGRES_INTEGRATION_URL"])
    with engine.connect() as connection:
        assert connection.execute(text("select 1")).scalar_one() == 1
        tables = {
            row[0]
            for row in connection.execute(
                text("select tablename from pg_catalog.pg_tables where schemaname = 'public'")
            )
        }
    assert {
        "users",
        "user_password_credentials",
        "auth_sessions",
        "organisation_invitations",
        "password_reset_tokens",
    } <= tables
