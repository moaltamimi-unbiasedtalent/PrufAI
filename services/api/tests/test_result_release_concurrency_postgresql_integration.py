"""Actual overlapping PostgreSQL release/artifact service transactions."""

from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import CertificateRecord, ResultRelease
from app.models.enums import ReleaseStatus
from app.result_release.service import ResultReleaseError, ResultReleaseService
from tests.integration_support.result_release_scenario import build_governed_release_scenario
from tests.test_result_release_postgresql_integration import (
    _url,
    issuance_enabled,  # noqa: F401 - shared explicit opt-in fixture
    release_approved,
    synthetic_authorization,
)

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for release transaction contention",
)


def _assert_real_blocker(
    engine: Engine, *, waiter: int, blocker: int, completed: threading.Event
) -> None:
    """Observe PostgreSQL's lock graph before permitting the winner to commit.

    Event waits bound synchronization; elapsed time alone never proves overlap.
    No application mocks, scratch tables, or timing-based success assertions.
    """
    deadline = time.monotonic() + 5
    with engine.connect() as observer:
        while time.monotonic() < deadline:
            blockers = observer.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": waiter})
            if blocker in blockers:
                return
            assert not completed.is_set(), "competing service finished without real contention"
            completed.wait(timeout=0.01)
    pytest.fail("competing service never waited on the winner's PostgreSQL transaction")


def _compete(
    engine: Engine,
    operation: Callable[[Session], object],
    backend_pid: queue.Queue[int],
    completed: threading.Event,
) -> tuple[str, str]:
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        try:
            session.execute(text("SET LOCAL lock_timeout = '8s'"))
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            backend_pid.put(session.scalar(text("SELECT pg_backend_pid()")))
            value = operation(session)
            session.commit()
            return "unexpected_success", str(getattr(value, "id", value))
        except ResultReleaseError as exc:
            session.rollback()
            return "conflict", str(exc)
        finally:
            completed.set()


def _run_overlap(
    engine: Engine,
    winner: Session,
    operation: Callable[[Session], object],
) -> str:
    winner_pid = winner.scalar(text("SELECT pg_backend_pid()"))
    backend_pid: queue.Queue[int] = queue.Queue(maxsize=1)
    completed = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        competing = executor.submit(_compete, engine, operation, backend_pid, completed)
        try:
            waiter = backend_pid.get(timeout=5)
            assert waiter != winner_pid
            _assert_real_blocker(engine, waiter=waiter, blocker=winner_pid, completed=completed)
            winner.commit()
            outcome, message = competing.result(timeout=12)
            assert outcome == "conflict", message
            return message
        finally:
            # Always unblock the second real connection on an assertion failure.
            winner.rollback()


def test_overlapping_release_service_transactions_have_one_authoritative_release() -> None:
    engine = create_engine(_url(), pool_size=4)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as bootstrap:
            scenario = build_governed_release_scenario(bootstrap)
            bootstrap.commit()
        with factory() as winner:
            release = ResultReleaseService.release(
                winner, scenario.context, scenario.version.id, operation_id="winning-release"
            )
            message = _run_overlap(
                engine,
                winner,
                lambda competing: ResultReleaseService.release(
                    competing,
                    scenario.context,
                    scenario.version.id,
                    operation_id="conflicting-release",
                ),
            )
            assert "release" in message.lower() or "stale" in message.lower()
            with factory() as inspect:
                rows = list(
                    inspect.scalars(
                        select(ResultRelease).where(
                            ResultRelease.assessment_result_id == scenario.result.id
                        )
                    )
                )
                assert len(rows) == 1
                assert rows[0].id == release.id
                assert rows[0].status is ReleaseStatus.RELEASED
                assert rows[0].supersedes_release_id is None
                assert (
                    inspect.scalar(
                        select(func.count())
                        .select_from(CertificateRecord)
                        .where(CertificateRecord.release_id == release.id)
                    )
                    == 0
                )
                # Keep a distinct sequential stale/conflicting operation proof.
                with pytest.raises(ResultReleaseError):
                    ResultReleaseService.release(
                        inspect,
                        scenario.context,
                        scenario.version.id,
                        operation_id=f"sequential-stale-{uuid.uuid4()}",
                    )
    finally:
        engine.dispose()


@pytest.mark.usefixtures("issuance_enabled")
def test_overlapping_certificate_service_transactions_cannot_duplicate_artifact() -> None:
    engine = create_engine(_url(), pool_size=4)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as bootstrap:
            scenario = build_governed_release_scenario(bootstrap)
            auth = synthetic_authorization(bootstrap, scenario)
            release = release_approved(bootstrap, scenario)
            bootstrap.commit()
        with factory() as winner:
            certificate = ResultReleaseService.certificate(
                winner,
                scenario.context,
                release.id,
                auth.id,
                operation_id="winning-certificate",
            )
            _run_overlap(
                engine,
                winner,
                lambda competing: ResultReleaseService.certificate(
                    competing,
                    scenario.context,
                    release.id,
                    auth.id,
                    operation_id="conflicting-certificate",
                ),
            )
            with factory() as inspect:
                certificates = list(
                    inspect.scalars(
                        select(CertificateRecord).where(CertificateRecord.release_id == release.id)
                    )
                )
                assert len(certificates) == 1
                assert certificates[0].id == certificate.id
                assert certificates[0].authorization_id == auth.id
                assert certificates[0].authorization_version == auth.version
                assert (
                    inspect.scalar(
                        select(func.count())
                        .select_from(ResultRelease)
                        .where(
                            ResultRelease.assessment_result_id == scenario.result.id,
                            ResultRelease.status == ReleaseStatus.RELEASED,
                        )
                    )
                    == 1
                )
    finally:
        engine.dispose()
