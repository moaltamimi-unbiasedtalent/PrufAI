"""Executed service acceptance against PostgreSQL created only by Alembic."""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Iterator

import psycopg
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.authorization import AuthorizationError, TenantContext
from app.config import get_settings
from app.models import (
    AuditEvent,
    CertificateRecord,
    CertificationAuthorization,
    ResultAppealVersion,
    ResultRelease,
    ResultVersion,
    StatementOfResults,
)
from app.models.enums import (
    AppealDecision,
    AppealStatus,
    AssessmentClassification,
    CorrectionStatus,
    CorrectionType,
    ReleaseStatus,
    ResultVersionStatus,
)
from app.result_governance.service import ResultGovernanceService
from app.result_release.service import ResultReleaseError, ResultReleaseService
from app.runtime.service import CandidateRuntimeService
from app.scoring.service import ScoringError, ScoringService
from tests.alembic_helpers import repository_alembic_head
from tests.integration_support.result_correction_scenario import (
    build_governed_result_correction_scenario,
    validate_governed_result_correction_scenario,
)
from tests.integration_support.result_release_scenario import (
    GovernedReleaseScenario,
    build_governed_release_scenario,
    release_context,
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


@pytest.fixture
def release_session() -> Iterator[Session]:
    engine = create_engine(_url())
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        try:
            yield session
        finally:
            session.rollback()
    engine.dispose()


@pytest.fixture
def issuance_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CERTIFICATION_ISSUANCE_ENABLED", "true")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def synthetic_authorization(
    session: Session,
    scenario: GovernedReleaseScenario,
    *,
    effective_from: datetime | None = None,
    effective_until: datetime | None = None,
    assessment_scope: str = "PRACTICE",
    provider_scope: str | None = None,
    standard_pack_version_id: uuid.UUID | None = None,
) -> CertificationAuthorization:
    """A platform-authorized synthetic pack, never a real provider entitlement."""
    return ResultReleaseService.authorize(
        session,
        replace(scenario.context, platform_permissions=frozenset({"certification.authorize"})),
        assessment_scope=assessment_scope,
        provider_scope=provider_scope
        or scenario.historical.standard_pack_version.standard_pack.code,
        standard_pack_version_id=standard_pack_version_id
        or scenario.historical.standard_pack_version.id,
        evidence_reference="SYNTHETIC TEST AUTHORIZATION - NOT OFFICIAL MATERIAL",
        effective_from=effective_from or datetime.now(timezone.utc) - timedelta(minutes=1),
        effective_until=effective_until,
        operation_id=f"synthetic-authorization-{uuid.uuid4()}",
    )


def release_approved(session: Session, scenario: GovernedReleaseScenario) -> ResultRelease:
    assert scenario.version.status is ResultVersionStatus.APPROVED
    release = ResultReleaseService.release(
        session, scenario.context, scenario.version.id, operation_id=f"release-{uuid.uuid4()}"
    )
    persisted = session.get(ResultVersion, scenario.version.id)
    assert persisted is not None and persisted.status is ResultVersionStatus.RELEASED
    return release


def test_result_release_schema_is_migration_backed() -> None:
    with psycopg.connect(_url().replace("postgresql+psycopg://", "postgresql://")) as connection:
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
                """SELECT table_name, constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'public' AND table_name IN
                ('result_releases', 'statements_of_results', 'certificate_records')"""
            )
        }
        assert ("result_releases", "uq_result_release_operation") in constraints
        assert ("result_releases", "uq_result_release_version") in constraints
        assert ("result_releases", "uq_release_successor") in constraints
        assert ("statements_of_results", "uq_statement_release") in constraints
        assert ("certificate_records", "uq_certificate_release") in constraints
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [
            (repository_alembic_head(),)
        ]


def test_legacy_step_one_provenance_is_preserved(release_session: Session) -> None:
    scenario = build_governed_result_correction_scenario(release_session)
    validate_governed_result_correction_scenario(release_session, scenario)
    context = release_context(scenario.organisation.id, scenario.reviewer.id)
    old_result_checksum = scenario.result_version_r1.checksum
    old_run_checksum = scenario.scoring_run_s1.checksum
    release = ResultReleaseService.release(
        release_session, context, scenario.result_version_r1.id, operation_id="legacy-release"
    )
    release_session.expire_all()
    assert release.organisation_id == scenario.organisation.id
    assert release.result_version_id == scenario.result_version_r1.id
    assert release.scoring_run_id == scenario.scoring_run_s1.id
    assert scenario.result_version_r1.checksum == old_result_checksum
    assert scenario.scoring_run_s1.checksum == old_run_checksum


def test_governed_release_statement_and_replay_persist_without_audit_read(
    release_session: Session,
) -> None:
    scenario = build_governed_release_scenario(release_session)
    release = release_approved(release_session, scenario)
    statement = ResultReleaseService.statement(
        release_session, scenario.context, release.id, operation_id="statement-1"
    )
    assert "audit.read" not in scenario.context.permissions
    ids = (release.id, statement.id, release.checksum, statement.checksum)
    release_session.commit()
    release_session.expire_all()
    same = ResultReleaseService.release(
        release_session, scenario.context, scenario.version.id, operation_id=release.operation_id
    )
    same_statement = ResultReleaseService.statement(
        release_session, scenario.context, same.id, operation_id="statement-1"
    )
    assert (same.id, same_statement.id, same.checksum, same_statement.checksum) == ids
    assert same.organisation_id == scenario.historical.organisation.id
    assert same.candidate_user_id == scenario.attempt.candidate_user_id
    assert same.attempt_id == scenario.attempt.id
    assert same.assessment_result_id == scenario.result.id
    assert same.result_version_id == scenario.version.id
    assert same.scoring_run_id == scenario.run.id
    assert same.standard_pack_version_id == scenario.historical.standard_pack_version.id
    assert same_statement.result_version_id == same.result_version_id
    assert same_statement.scoring_run_id == same.scoring_run_id
    assert same_statement.standard_pack_version_id == same.standard_pack_version_id
    assert same_statement.candidate_user_id == same.candidate_user_id
    assert same_statement.attempt_id == same.attempt_id
    events = set(
        release_session.scalars(
            select(AuditEvent.event_type).where(
                AuditEvent.organisation_id == scenario.context.organisation_id
            )
        )
    )
    assert {"result.release_created", "statement.created"} <= events


def test_replays_and_statement_issuance_authorize_before_returning_data(
    release_session: Session, issuance_enabled: None
) -> None:
    scenario = build_governed_release_scenario(release_session)
    auth = synthetic_authorization(release_session, scenario)
    release = release_approved(release_session, scenario)
    statement = ResultReleaseService.statement(
        release_session, scenario.context, release.id, operation_id="statement-1"
    )
    certificate = ResultReleaseService.certificate(
        release_session, scenario.context, release.id, auth.id, operation_id="certificate-1"
    )
    foreign = replace(
        scenario.context,
        organisation_id=uuid.uuid4(),
        platform_permissions=frozenset({"certification.authorize"}),
    )
    for action in (
        lambda: ResultReleaseService.release(
            release_session, foreign, scenario.version.id, operation_id=release.operation_id
        ),
        lambda: ResultReleaseService.statement(
            release_session, foreign, release.id, operation_id=statement.operation_id
        ),
        lambda: ResultReleaseService.certificate(
            release_session, foreign, release.id, auth.id, operation_id=certificate.operation_id
        ),
        lambda: ResultReleaseService.revoke_release(
            release_session, foreign, release.id, "cross-tenant replay"
        ),
        lambda: ResultReleaseService.revoke_authorization(
            release_session, foreign, auth.id, "cross-tenant replay"
        ),
    ):
        with pytest.raises((ResultReleaseError, AuthorizationError)):
            action()
    for user_id in (scenario.context.user_id, scenario.candidate_context.user_id):
        read_only = replace(
            scenario.context, user_id=user_id, permissions=frozenset({"result.read"})
        )
        with pytest.raises(AuthorizationError):
            ResultReleaseService.statement(
                release_session, read_only, release.id, operation_id=statement.operation_id
            )
        with pytest.raises(AuthorizationError):
            ResultReleaseService.release(
                release_session, read_only, scenario.version.id, operation_id=release.operation_id
            )
        with pytest.raises(AuthorizationError):
            ResultReleaseService.certificate(
                release_session,
                read_only,
                release.id,
                auth.id,
                operation_id=certificate.operation_id,
            )
        with pytest.raises(AuthorizationError):
            ResultReleaseService.revoke_release(
                release_session, read_only, release.id, "unauthorized replay"
            )
    assert ResultReleaseService.candidate_releases(release_session, scenario.candidate_context) == [
        release
    ]
    assert (
        ResultReleaseService.candidate_releases(
            release_session,
            replace(scenario.candidate_context, user_id=scenario.historical.candidate.id),
        )
        == []
    )


def test_synthetic_certificate_uses_normal_scoring_human_release_and_pins_authorization(
    release_session: Session, issuance_enabled: None
) -> None:
    scenario = build_governed_release_scenario(release_session)
    assert scenario.version.id != scenario.historical.result_version_r1.id
    assert scenario.version.released_at is None
    auth = synthetic_authorization(release_session, scenario)
    release = release_approved(release_session, scenario)
    certificate = ResultReleaseService.certificate(
        release_session, scenario.context, release.id, auth.id, operation_id="certificate-1"
    )
    checksum = certificate.checksum
    release_session.flush()
    release_session.expire_all()
    same = ResultReleaseService.certificate(
        release_session, scenario.context, release.id, auth.id, operation_id="certificate-1"
    )
    assert same.id == certificate.id
    assert same.release_id == release.id
    assert same.result_version_id == scenario.version.id
    assert same.authorization_id == auth.id
    assert same.authorization_version == auth.version
    assert same.checksum == checksum
    assert "SYNTHETIC TEST" in auth.evidence_reference
    assert auth.assessment_scope == "PRACTICE"
    assert auth.standard_pack_version_id == release.standard_pack_version_id
    assert auth.provider_scope == scenario.historical.standard_pack_version.standard_pack.code
    events = set(
        release_session.scalars(
            select(AuditEvent.event_type).where(
                AuditEvent.organisation_id == scenario.context.organisation_id
            )
        )
    )
    assert {"certification.authorization_created", "certificate.issued"} <= events
    different_authorization = synthetic_authorization(release_session, scenario)
    with pytest.raises(ResultReleaseError, match="authorization does not match"):
        ResultReleaseService.certificate(
            release_session,
            scenario.context,
            release.id,
            different_authorization.id,
            operation_id="certificate-1",
        )


def test_release_authorization_statement_and_certificate_history_is_immutable(
    release_session: Session, issuance_enabled: None
) -> None:
    scenario = build_governed_release_scenario(release_session)
    authorization = synthetic_authorization(release_session, scenario)
    release = release_approved(release_session, scenario)
    statement = ResultReleaseService.statement(
        release_session, scenario.context, release.id, operation_id="statement-1"
    )
    certificate = ResultReleaseService.certificate(
        release_session,
        scenario.context,
        release.id,
        authorization.id,
        operation_id="certificate-1",
    )
    snapshots = [
        (entity.id, entity.checksum) for entity in (release, authorization, statement, certificate)
    ]
    for entity, field, changed in (
        (release, "scoring_run_id", uuid.uuid4()),
        (authorization, "provider_scope", "FORGED SYNTHETIC AUTHORITY"),
        (statement, "payload", {"forged": "statement"}),
        (certificate, "authorization_version", authorization.version + 1),
    ):
        with pytest.raises(ValueError, match="immutable"):
            with release_session.begin_nested():
                setattr(entity, field, changed)
                release_session.flush()
        with pytest.raises(ValueError, match="history cannot be deleted"):
            with release_session.begin_nested():
                release_session.delete(entity)
                release_session.flush()
    release_session.expire_all()
    assert [
        (entity.id, entity.checksum) for entity in (release, authorization, statement, certificate)
    ] == snapshots


def test_authorization_operation_identity_is_tenant_and_permission_scoped(
    release_session: Session, issuance_enabled: None
) -> None:
    scenario = build_governed_release_scenario(release_session)
    authorization = synthetic_authorization(release_session, scenario)
    authority = replace(
        scenario.context, platform_permissions=frozenset({"certification.authorize"})
    )

    def replay(
        context: TenantContext, evidence: str = authorization.evidence_reference
    ) -> CertificationAuthorization:
        return ResultReleaseService.authorize(
            release_session,
            context,
            assessment_scope=authorization.assessment_scope,
            provider_scope=authorization.provider_scope,
            standard_pack_version_id=authorization.standard_pack_version_id,
            operation_id=authorization.operation_id,
            evidence_reference=evidence,
            effective_from=authorization.effective_from,
            effective_until=authorization.effective_until,
        )

    assert replay(authority).id == authorization.id
    with pytest.raises(AuthorizationError):
        replay(scenario.context)
    with pytest.raises(ResultReleaseError, match="different scope"):
        replay(authority, "SYNTHETIC TEST changed operation payload")
    other = build_governed_result_correction_scenario(release_session)
    foreign_authority = replace(authority, organisation_id=other.organisation.id)
    independent = replay(foreign_authority)
    assert independent.id != authorization.id
    assert independent.organisation_id == other.organisation.id
    assert authorization.organisation_id == scenario.context.organisation_id


@pytest.mark.parametrize("invalid", ["expired", "future", "revoked", "scope", "provider", "pack"])
def test_certificate_denies_inactive_or_wrong_scope_authorization(
    release_session: Session, issuance_enabled: None, invalid: str
) -> None:
    scenario = build_governed_release_scenario(release_session)
    release = release_approved(release_session, scenario)
    now = datetime.now(timezone.utc)
    if invalid == "expired":
        auth = synthetic_authorization(
            release_session,
            scenario,
            effective_from=now - timedelta(days=2),
            effective_until=now - timedelta(days=1),
        )
    elif invalid == "future":
        auth = synthetic_authorization(
            release_session, scenario, effective_from=now + timedelta(days=1)
        )
    elif invalid == "revoked":
        auth = synthetic_authorization(release_session, scenario)
        ResultReleaseService.revoke_authorization(
            release_session,
            replace(scenario.context, platform_permissions=frozenset({"certification.authorize"})),
            auth.id,
            "synthetic test revocation",
        )
    elif invalid == "scope":
        with pytest.raises(ResultReleaseError, match="scope|CERTIFICATION.NOT_AUTHORIZED"):
            synthetic_authorization(
                release_session, scenario, assessment_scope="OFFICIAL_CERTIFICATION"
            )
        return
    elif invalid == "provider":
        with pytest.raises(ResultReleaseError, match="scope"):
            synthetic_authorization(
                release_session, scenario, provider_scope="UNRELATED_SYNTHETIC_PROVIDER"
            )
        return
    else:
        other = build_governed_result_correction_scenario(release_session)
        auth = synthetic_authorization(
            release_session,
            scenario,
            standard_pack_version_id=other.standard_pack_version.id,
            provider_scope=other.standard_pack_version.standard_pack.code,
        )
    release_session.flush()
    with pytest.raises(ResultReleaseError, match="CERTIFICATION.NOT_AUTHORIZED"):
        ResultReleaseService.certificate(
            release_session, scenario.context, release.id, auth.id, operation_id="blocked"
        )
    assert (
        release_session.scalar(
            select(CertificateRecord).where(CertificateRecord.release_id == release.id)
        )
        is None
    )


def test_default_configuration_and_institution_role_cannot_grant_certification(
    release_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = build_governed_release_scenario(release_session)
    monkeypatch.setenv("CERTIFICATION_ISSUANCE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        with pytest.raises((ResultReleaseError, AuthorizationError)):
            synthetic_authorization(release_session, scenario)
        with pytest.raises((ResultReleaseError, AuthorizationError)):
            ResultReleaseService.authorize(
                release_session,
                scenario.context,
                assessment_scope="PRACTICE",
                provider_scope=scenario.historical.standard_pack_version.standard_pack.code,
                standard_pack_version_id=scenario.historical.standard_pack_version.id,
                evidence_reference="SYNTHETIC TEST AUTHORIZATION - NOT OFFICIAL MATERIAL",
                effective_from=datetime.now(timezone.utc),
                operation_id=f"institution-denied-{uuid.uuid4()}",
            )
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("provider_hint", ["Goethe", "telc", "g.a.s.t.", "DTZ"])
def test_provider_and_cefr_hints_never_authorize_official_release(
    release_session: Session, issuance_enabled: None, provider_hint: str
) -> None:
    scenario = build_governed_release_scenario(
        release_session,
        classification=AssessmentClassification.OFFICIAL_CERTIFICATION,
        provider_hint=provider_hint,
    )
    assert scenario.version.status is ResultVersionStatus.APPROVED
    assert scenario.version.released_at is None
    assert scenario.historical.standard_pack_version.supported_levels == ["B1"]
    with pytest.raises(ValueError, match="official certification delivery is not authorized"):
        CandidateRuntimeService._safe_plan(release_session, scenario.exam_version)
    with pytest.raises(ScoringError, match="CERTIFICATION.NOT_AUTHORIZED"):
        ScoringService.release(release_session, scenario.context, scenario.version.id)
    with pytest.raises(ResultReleaseError, match="CERTIFICATION.NOT_AUTHORIZED"):
        ResultReleaseService.release(
            release_session,
            scenario.context,
            scenario.version.id,
            operation_id=f"official-denied-{uuid.uuid4()}",
        )
    assert (
        release_session.scalar(
            select(ResultRelease).where(ResultRelease.result_version_id == scenario.version.id)
        )
        is None
    )


def test_revoked_release_cannot_reissue_artifacts_and_history_remains(
    release_session: Session, issuance_enabled: None
) -> None:
    scenario = build_governed_release_scenario(release_session)
    auth = synthetic_authorization(release_session, scenario)
    release = release_approved(release_session, scenario)
    statement = ResultReleaseService.statement(
        release_session, scenario.context, release.id, operation_id="statement-1"
    )
    certificate = ResultReleaseService.certificate(
        release_session, scenario.context, release.id, auth.id, operation_id="certificate-1"
    )
    old = (release.checksum, statement.checksum, dict(statement.payload), certificate.checksum)
    ResultReleaseService.revoke_release(
        release_session, scenario.context, release.id, "synthetic lifecycle test"
    )
    for operation in ("statement-1", "new-statement"):
        with pytest.raises(ResultReleaseError):
            ResultReleaseService.statement(
                release_session, scenario.context, release.id, operation_id=operation
            )
    for operation in ("certificate-1", "new-certificate"):
        with pytest.raises(ResultReleaseError):
            ResultReleaseService.certificate(
                release_session, scenario.context, release.id, auth.id, operation_id=operation
            )
    assert ResultReleaseService.candidate_releases(release_session, scenario.candidate_context) == [
        release
    ]
    release_session.flush()
    release_session.expire_all()
    assert release.status is ReleaseStatus.REVOKED
    assert ResultReleaseService.effective_status(release_session, release) == "REVOKED"
    assert ResultReleaseService.artifact_status(release_session, release, statement) == "REVOKED"
    assert ResultReleaseService.artifact_status(release_session, release, certificate) == "REVOKED"
    assert (release.checksum, statement.checksum, statement.payload, certificate.checksum) == old
    assert release_session.get(StatementOfResults, statement.id) is not None
    assert release_session.get(CertificateRecord, certificate.id) is not None
    assert release in ResultReleaseService.organisation_releases(release_session, scenario.context)
    events = set(
        release_session.scalars(
            select(AuditEvent.event_type).where(AuditEvent.entity_id == str(release.id))
        )
    )
    assert {"result.release_created", "result.release_revoked"} <= events


def test_authorization_revocation_invalidates_certificate_without_mutating_history(
    release_session: Session, issuance_enabled: None
) -> None:
    scenario = build_governed_release_scenario(release_session)
    authorization = synthetic_authorization(release_session, scenario)
    release = release_approved(release_session, scenario)
    certificate = ResultReleaseService.certificate(
        release_session,
        scenario.context,
        release.id,
        authorization.id,
        operation_id="certificate-1",
    )
    before = (certificate.checksum, certificate.authorization_id, certificate.authorization_version)
    authority = replace(
        scenario.context, platform_permissions=frozenset({"certification.authorize"})
    )
    ResultReleaseService.revoke_authorization(
        release_session, authority, authorization.id, "synthetic authority withdrawn"
    )
    ResultReleaseService.revoke_authorization(
        release_session, authority, authorization.id, "synthetic authority withdrawn"
    )
    assert ResultReleaseService.artifact_status(release_session, release, certificate) == "REVOKED"
    assert ResultReleaseService.effective_status(release_session, release) == "RELEASED"
    with pytest.raises(ResultReleaseError):
        ResultReleaseService.certificate(
            release_session,
            scenario.context,
            release.id,
            authorization.id,
            operation_id="certificate-1",
        )
    release_session.flush()
    release_session.expire_all()
    assert (
        certificate.checksum,
        certificate.authorization_id,
        certificate.authorization_version,
    ) == before
    events = list(
        release_session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == str(authorization.id),
                AuditEvent.event_type == "certification.authorization_revoked",
            )
        )
    )
    assert len(events) == 1
    assert events[0].reason == "synthetic authority withdrawn"


def test_corrected_successor_requires_exact_governed_lineage_and_preserves_history(
    release_session: Session,
) -> None:
    scenario = build_governed_release_scenario(release_session)
    old_release = release_approved(release_session, scenario)
    old_statement = ResultReleaseService.statement(
        release_session, scenario.context, old_release.id, operation_id="old-statement"
    )
    original = (
        scenario.version.checksum,
        scenario.run.checksum,
        old_release.checksum,
        old_statement.checksum,
        dict(old_statement.payload),
    )
    appeal = ResultGovernanceService.submit_appeal(
        release_session,
        scenario.candidate_context,
        result_version_id=scenario.version.id,
        category="CORRECTION",
        grounds="SYNTHETIC objective key correction.",
        operation_id=f"appeal-{uuid.uuid4()}",
    )
    ResultGovernanceService.transition(
        release_session, scenario.context, appeal.id, AppealStatus.UNDER_REVIEW
    )
    appeal_version = release_session.scalar(
        select(ResultAppealVersion).where(
            ResultAppealVersion.appeal_id == appeal.id,
            ResultAppealVersion.version_number == appeal.current_version_number,
        )
    )
    assert appeal_version is not None
    ResultGovernanceService.adjudicate(
        release_session,
        scenario.context,
        appeal_id=appeal.id,
        appeal_version_number=appeal.current_version_number,
        appeal_checksum=appeal.current_checksum,
        evidence_bundle_checksum=appeal_version.evidence_bundle_checksum,
        decision=AppealDecision.UPHOLD,
        operation_id=f"adjudicate-{uuid.uuid4()}",
        reason="SYNTHETIC correction upheld.",
    )
    release_session.flush()
    decision = release_session.scalar(
        select(ResultAppealVersion).where(
            ResultAppealVersion.appeal_id == appeal.id,
            ResultAppealVersion.version_number == appeal.current_version_number,
        )
    )
    assert decision is not None
    correction = ResultGovernanceService.initiate_correction(
        release_session,
        scenario.context,
        appeal_id=appeal.id,
        appeal_version_number=appeal.current_version_number,
        appeal_checksum=appeal.current_checksum,
        evidence_bundle_checksum=decision.evidence_bundle_checksum,
        correction_type=CorrectionType.OBJECTIVE_RESCORING,
        objective_bindings=[
            {
                "original_item_version_id": str(scenario.item_version.id),
                "replacement_item_version_id": str(
                    scenario.historical.objective_item_version_i2.id
                ),
            }
        ],
        operation_id=f"correction-{uuid.uuid4()}",
    )
    assert correction.status is CorrectionStatus.RESCORED
    successor = release_session.get(ResultVersion, correction.new_result_version_id)
    assert successor is not None and successor.id != scenario.version.id
    assert successor.supersedes_version_id == scenario.version.id
    assert successor.scoring_run_id == correction.new_scoring_run_id
    assert successor.result_id == scenario.result.id
    ScoringService.request_review(release_session, scenario.context, successor.id)
    ScoringService.approve_review(release_session, scenario.context, successor.id)
    unrelated = build_governed_release_scenario(release_session, historical=scenario.historical)
    unrelated_release = release_approved(release_session, unrelated)
    assert unrelated_release.organisation_id == old_release.organisation_id
    assert unrelated_release.candidate_user_id == old_release.candidate_user_id
    assert unrelated_release.attempt_id != old_release.attempt_id
    for predecessor in (None, uuid.uuid4(), unrelated_release.id):
        with pytest.raises(ResultReleaseError):
            ResultReleaseService.release(
                release_session,
                scenario.context,
                successor.id,
                operation_id=f"invalid-successor-{uuid.uuid4()}",
                supersedes_release_id=predecessor,
            )
    new_release = ResultReleaseService.release(
        release_session,
        scenario.context,
        successor.id,
        operation_id="corrected-release",
        supersedes_release_id=old_release.id,
    )
    release_session.flush()
    release_session.expire_all()
    assert new_release.supersedes_release_id == old_release.id
    assert new_release.candidate_user_id == old_release.candidate_user_id
    assert new_release.attempt_id == old_release.attempt_id
    assert new_release.assessment_result_id == old_release.assessment_result_id
    assert new_release.standard_pack_version_id == old_release.standard_pack_version_id
    assert new_release.scoring_run_id == correction.new_scoring_run_id
    assert old_release.status is ReleaseStatus.SUPERSEDED
    assert (
        ResultReleaseService.artifact_status(release_session, old_release, old_statement)
        == "SUPERSEDED"
    )
    assert (
        scenario.version.checksum,
        scenario.run.checksum,
        old_release.checksum,
        old_statement.checksum,
        old_statement.payload,
    ) == original
    candidate_history = ResultReleaseService.candidate_releases(
        release_session, scenario.candidate_context
    )
    assert {item.id for item in candidate_history} == {
        new_release.id,
        old_release.id,
        unrelated_release.id,
    }
    assert [
        item.id
        for item in candidate_history
        if item.assessment_result_id == scenario.result.id
        and ResultReleaseService.effective_status(release_session, item) == "RELEASED"
    ] == [new_release.id]
    with pytest.raises(ResultReleaseError):
        ResultReleaseService.statement(
            release_session, scenario.context, old_release.id, operation_id="stale-artifact"
        )
    with pytest.raises(ResultReleaseError):
        ResultReleaseService.release(
            release_session, scenario.context, scenario.version.id, operation_id="stale-release"
        )
    assert (
        release_session.scalar(
            select(AuditEvent.id).where(
                AuditEvent.entity_id == str(old_release.id),
                AuditEvent.event_type == "result.release_superseded",
            )
        )
        is not None
    )
