import os

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL"),
    reason="PostgreSQL/pgvector integration service is configured in CI only",
)
def test_postgresql_pgvector_retrieval_schema_is_ready() -> None:
    engine = create_engine(os.environ["POSTGRES_INTEGRATION_URL"])
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("select 1 from pg_extension where extname = 'vector'")
            ).scalar_one()
            == 1
        )
        tables = {
            row[0]
            for row in connection.execute(
                text("select tablename from pg_catalog.pg_tables where schemaname = 'public'")
            )
        }
    assert {
        "knowledge_chunks",
        "knowledge_chunk_embeddings",
        "knowledge_index_profiles",
        "knowledge_index_jobs",
    } <= tables
