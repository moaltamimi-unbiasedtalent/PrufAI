"""Migration-backed PostgreSQL proof for the Phase 8 Step 2 release lifecycle."""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.authorization import TenantContext
from app.models import CertificateRecord, ResultRelease
from app.models.enums import AssessmentClassification, ReleaseStatus
from app.result_release.service import ResultReleaseError, ResultReleaseService
from tests.alembic_helpers import repository_alembic_head
from tests.integration_support.result_correction_scenario import (
    build_governed_result_correction_scenario,
    validate_governed_result_correction_scenario,
)

REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for result-release integration",
)


def _url() -> str:
    configured = os.getenv("POSTGRES_INTEGRATION_URL")
    if not configured:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    return configured


def _context(
    organisation_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    permissions: set[str] | None = None,
):
    return TenantContext(
        user_id=user_id,
        organisation_id=organisation_id,
        membership_id=uuid.uuid4(),
        permissions=frozenset(
            permissions or {"result.release", "result.manage", "result.read", "audit.read"}
        ),
    )


def _session():
    engine = create_engine(_url())
    return engine, sessionmaker(bind=engine, expire_on_commit=False)()


def test_result_release_schema_is_migration_backed() -> None:
    url = _url().replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        assert {
            "result_releases",
            "statements_of_results",
            "certification_authorizations",
            "certificate_records",
        } <= tables
        constraints = {
            (row[0], row[1])
            for row in connection.execute(
                """
                SELECT table_name, constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'public' AND table_name IN
                ('result_releases', 'statements_of_results', 'certificate_records')
                """
            )
        }
        assert ("result_releases", "uq_result_release_operation") in constraints
        assert ("result_releases", "uq_result_release_version") in constraints
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [
            (repository_alembic_head(),)
        ]


def test_basic_release_statement_and_idempotency_are_persisted() -> None:
    engine, session = _session()
    try:
        scenario = build_governed_result_correction_scenario(session)
        validate_governed_result_correction_scenario(session, scenario)
        context = _context(scenario.org.id, scenario.reviewer.id)
        release = ResultReleaseService.release(
            session, context, scenario.result_version_r1.id, operation_id="release-1"
        )
        same = ResultReleaseService.release(
            session, context, scenario.result_version_r1.id, operation_id="release-1"
        )
        assert same.id == release.id
        assert release.result_version_id == scenario.result_version_r1.id
        assert release.scoring_run_id == scenario.scoring_run_s1.id
        assert release.standard_pack_version_id == scenario.standard_pack_version.id
        assert release.organisation_id == scenario.org.id
        assert release.attempt_id == scenario.attempt.id
        assert release.checksum
        statement = ResultReleaseService.statement(
            session, context, release.id, operation_id="statement-1"
        )
        assert statement.release_id == release.id
        assert statement.result_version_id == scenario.result_version_r1.id
        assert statement.scoring_run_id == scenario.scoring_run_s1.id
        assert statement.standard_pack_version_id == scenario.standard_pack_version.id
        assert "reviewer" not in statement.payload
        persisted = session.scalar(
            select(ResultRelease).where(
                ResultRelease.result_version_id == release.result_version_id
            )
        )
        assert persisted is not None and persisted.id == release.id
        session.rollback()
    finally:
        session.close()
        engine.dispose()


def test_release_is_cross_tenant_denied_and_official_is_default_deny() -> None:
    engine, session = _session()
    try:
        scenario = build_governed_result_correction_scenario(session)
        with pytest.raises(ResultReleaseError, match="not available"):
            ResultReleaseService.release(
                session,
                _context(uuid.uuid4(), scenario.reviewer.id),
                scenario.result_version_r1.id,
                operation_id="cross-tenant",
            )
        scenario.exam_version.classification = AssessmentClassification.OFFICIAL_CERTIFICATION
        with pytest.raises(ResultReleaseError, match="CERTIFICATION.NOT_AUTHORIZED"):
            ResultReleaseService.release(
                session,
                _context(scenario.org.id, scenario.reviewer.id),
                scenario.result_version_r1.id,
                operation_id="official-deny",
            )
        session.rollback()
    finally:
        session.close()
        engine.dispose()


def test_release_concurrency_has_one_authoritative_row() -> None:
    engine, seed = _session()
    try:
        scenario = build_governed_result_correction_scenario(seed)
        seed.commit()
        first = sessionmaker(bind=engine, expire_on_commit=False)()
        second = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            context = _context(scenario.org.id, scenario.reviewer.id)
            release = ResultReleaseService.release(
                first, context, scenario.result_version_r1.id, operation_id="concurrent-1"
            )
            first.commit()
            with pytest.raises(ResultReleaseError, match="already has an authoritative release"):
                ResultReleaseService.release(
                    second, context, scenario.result_version_r1.id, operation_id="concurrent-2"
                )
            assert (
                second.scalar(
                    select(ResultRelease).where(
                        ResultRelease.result_version_id == scenario.result_version_r1.id,
                        ResultRelease.status == ReleaseStatus.RELEASED,
                    )
                ).id
                == release.id
            )
        finally:
            first.close()
            second.close()
    finally:
        seed.close()
        engine.dispose()


def test_synthetic_authorization_allows_official_certificate_and_is_idempotent() -> None:
    engine, session = _session()
    try:
        scenario = build_governed_result_correction_scenario(session)
        scenario.exam_version.classification = AssessmentClassification.OFFICIAL_CERTIFICATION
        context = _context(scenario.org.id, scenario.reviewer.id)
        authorization = ResultReleaseService.authorize(
            session,
            context,
            assessment_scope=AssessmentClassification.OFFICIAL_CERTIFICATION.value,
            provider_scope="PRUFAI_SYNTHETIC_TEST_AUTHORITY",
            evidence_reference="SYNTHETIC TEST AUTHORIZATION - NOT OFFICIAL MATERIAL",
            effective_from=scenario.exam_version.created_at,
        )
        release = ResultReleaseService.release(
            session, context, scenario.result_version_r1.id, operation_id="official-release"
        )
        certificate = ResultReleaseService.certificate(
            session, context, release.id, authorization.id, operation_id="certificate-1"
        )
        same = ResultReleaseService.certificate(
            session, context, release.id, authorization.id, operation_id="certificate-1"
        )
        assert same.id == certificate.id
        assert certificate.release_id == release.id
        assert certificate.result_version_id == scenario.result_version_r1.id
        assert certificate.authorization_id == authorization.id
        assert certificate.checksum
        assert (
            session.scalar(
                select(CertificateRecord).where(CertificateRecord.release_id == release.id)
            ).id
            == certificate.id
        )
        session.rollback()
    finally:
        session.close()
        engine.dispose()
