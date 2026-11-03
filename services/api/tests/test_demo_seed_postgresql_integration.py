"""Execute the synthetic demo against the migration-backed PostgreSQL schema."""

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.test_demo_seed import assert_demo_runtime_roundtrip
from tests.test_integrated_demo_seed import assert_golden_seed_preconditions

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for the demo runtime integration",
)


def test_demo_seed_runtime_is_usable_on_migrated_postgresql() -> None:
    url = os.getenv("POSTGRES_INTEGRATION_URL")
    if not url:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    engine = create_engine(url)
    try:
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            assert_demo_runtime_roundtrip(session)
    finally:
        engine.dispose()


def test_golden_seed_is_unassigned_and_valid_on_migrated_postgresql() -> None:
    url = os.getenv("POSTGRES_INTEGRATION_URL")
    if not url:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    engine = create_engine(url)
    try:
        with sessionmaker(bind=engine, expire_on_commit=False)() as session:
            assert_golden_seed_preconditions(session)
    finally:
        engine.dispose()
