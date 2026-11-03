"""Service-level release security tests; PostgreSQL acceptance is separate."""

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.authorization import AuthorizationError, TenantContext
from app.authorization.seed import seed_defaults
from app.config import Settings
from app.models import (
    AuditEvent,
    Base,
    CertificationAuthorization,
    Permission,
    ResultRelease,
    Role,
    RolePermission,
    StatementOfResults,
)
from app.models.enums import (
    AssessmentClassification,
    CertificationAuthorizationStatus,
    ModerationStatus,
    ResultVersionStatus,
    RoleScope,
)
from app.result_governance.service import ResultGovernanceService
from app.result_release.service import ResultReleaseError, ResultReleaseService
from tests.integration_support.result_correction_scenario import (
    build_governed_result_correction_scenario,
)
from tests.integration_support.result_release_scenario import build_governed_release_scenario


def release_context(scenario, *, permissions=None) -> TenantContext:
    return TenantContext(
        user_id=scenario.reviewer.id,
        organisation_id=scenario.organisation.id,
        membership_id=None,
        permissions=frozenset(
            {"result.release", "result.read", "result.manage", "result.review", "audit.read"}
            if permissions is None
            else permissions
        ),
    )


def recorded_legacy_release(session):
    """Record a genuine historical Step 1 release, without editing its provenance."""
    scenario = build_governed_result_correction_scenario(session)
    context = release_context(scenario)
    operation_id = str(uuid.uuid4())
    release = ResultReleaseService.release(
        session, context, scenario.result_version_r1.id, operation_id=operation_id
    )
    session.flush()
    return scenario, context, release, operation_id


def test_release_tables_are_registered():
    assert {
        "result_releases",
        "statements_of_results",
        "certification_authorizations",
        "certificate_records",
    } <= set(Base.metadata.tables)


def test_historical_release_records_exact_provenance_without_rewriting_results(session):
    scenario = build_governed_result_correction_scenario(session)
    context = release_context(scenario)
    result_checksum = scenario.result_version_r1.checksum
    run_checksum = scenario.scoring_run_s1.checksum
    release = ResultReleaseService.release(
        session, context, scenario.result_version_r1.id, operation_id=str(uuid.uuid4())
    )
    session.flush()
    session.expire_all()
    assert release.organisation_id == scenario.organisation.id
    assert release.candidate_user_id == scenario.candidate.id
    assert release.attempt_id == scenario.attempt.id
    assert release.assessment_result_id == scenario.assessment_result.id
    assert release.result_version_id == scenario.result_version_r1.id
    assert release.scoring_run_id == scenario.scoring_run_s1.id
    assert release.standard_pack_version_id == scenario.standard_pack_version.id
    assert release.checksum
    assert scenario.result_version_r1.checksum == result_checksum
    assert scenario.scoring_run_s1.checksum == run_checksum


def test_release_replay_authorizes_before_returning_existing_data(session):
    scenario, context, release, operation_id = recorded_legacy_release(session)
    same = ResultReleaseService.release(
        session, context, scenario.result_version_r1.id, operation_id=operation_id
    )
    assert same.id == release.id
    with pytest.raises(AuthorizationError):
        ResultReleaseService.release(
            session,
            replace(context, permissions=frozenset({"result.read"})),
            scenario.result_version_r1.id,
            operation_id=operation_id,
        )
    with pytest.raises(ResultReleaseError):
        ResultReleaseService.release(
            session,
            replace(context, organisation_id=uuid.uuid4()),
            scenario.result_version_r1.id,
            operation_id=operation_id,
        )
    assert list(session.scalars(select(ResultRelease))) == [release]


@pytest.mark.parametrize("candidate", [False, True])
def test_same_org_membership_or_result_read_cannot_issue_or_replay_statement(session, candidate):
    scenario, context, release, _ = recorded_legacy_release(session)
    statement_operation = str(uuid.uuid4())
    statement = ResultReleaseService.statement(
        session, context, release.id, operation_id=statement_operation
    )
    unauthorised = replace(
        context,
        user_id=scenario.candidate.id if candidate else scenario.second_candidate.id,
        permissions=frozenset({"organisation.read", "result.read"}),
    )
    for operation_id in (str(uuid.uuid4()), statement_operation):
        with pytest.raises(AuthorizationError):
            ResultReleaseService.statement(
                session, unauthorised, release.id, operation_id=operation_id
            )
    assert list(session.scalars(select(StatementOfResults))) == [statement]


def test_cross_tenant_statement_replay_is_denied(session):
    _, context, release, _ = recorded_legacy_release(session)
    operation_id = str(uuid.uuid4())
    statement = ResultReleaseService.statement(
        session, context, release.id, operation_id=operation_id
    )
    with pytest.raises(ResultReleaseError):
        ResultReleaseService.statement(
            session,
            replace(context, organisation_id=uuid.uuid4()),
            release.id,
            operation_id=operation_id,
        )
    assert list(session.scalars(select(StatementOfResults))) == [statement]


def test_release_and_statement_audit_do_not_require_audit_read_permission(session):
    scenario = build_governed_result_correction_scenario(session)
    context = release_context(scenario, permissions={"result.release", "result.manage"})
    release = ResultReleaseService.release(
        session, context, scenario.result_version_r1.id, operation_id=str(uuid.uuid4())
    )
    statement = ResultReleaseService.statement(
        session, context, release.id, operation_id=str(uuid.uuid4())
    )
    session.flush()
    rows = list(session.scalars(select(AuditEvent)))
    assert {row.event_type for row in rows} >= {"result.release_created", "statement.created"}
    assert {row.entity_id for row in rows} >= {str(release.id), str(statement.id)}


def test_institution_admin_cannot_grant_certification_authority(session, monkeypatch):
    scenario = build_governed_result_correction_scenario(session)
    monkeypatch.setattr(
        "app.result_release.service.get_settings",
        lambda: Settings(_env_file=None, environment="test", certification_issuance_enabled=True),
    )
    context = release_context(scenario)
    with pytest.raises(AuthorizationError):
        ResultReleaseService.authorize(
            session,
            context,
            assessment_scope=scenario.exam_version.classification.value,
            provider_scope=scenario.standard_pack_version.standard_pack.code,
            standard_pack_version_id=scenario.standard_pack_version.id,
            evidence_reference="SYNTHETIC TEST AUTHORIZATION - NOT OFFICIAL MATERIAL",
            effective_from=datetime.now(timezone.utc),
            operation_id=str(uuid.uuid4()),
        )
    assert list(session.scalars(select(CertificationAuthorization))) == []


def test_certificate_issuance_is_disabled_by_default_even_for_institution_admin(
    session, monkeypatch
):
    scenario, context, release, _ = recorded_legacy_release(session)
    monkeypatch.setattr(
        "app.result_release.service.get_settings",
        lambda: Settings(_env_file=None, environment="test"),
    )
    with pytest.raises(ResultReleaseError, match="DISABLED|disabled|NOT_AUTHORIZED"):
        ResultReleaseService.certificate(
            session,
            context,
            release.id,
            uuid.uuid4(),
            operation_id=str(uuid.uuid4()),
        )


def test_certification_permission_is_seeded_only_for_platform_super_admin(session):
    seed_defaults(session)
    permission = session.scalar(
        select(Permission).where(Permission.key == "certification.authorize")
    )
    assert permission is not None
    assert permission.scope is RoleScope.PLATFORM
    assigned_roles = list(
        session.scalars(
            select(Role)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .where(RolePermission.permission_id == permission.id)
        )
    )
    assert [(role.key, role.scope) for role in assigned_roles] == [
        ("PRUFAI_SUPER_ADMIN", RoleScope.PLATFORM)
    ]


def test_initial_release_is_blocked_by_an_open_moderation_case(session):
    scenario = build_governed_release_scenario(session)
    context = replace(
        scenario.context, permissions=scenario.context.permissions | {"result.moderate"}
    )
    case = ResultGovernanceService.create_moderation_case(
        session,
        context,
        result_version_id=scenario.version.id,
        reason="Synthetic unresolved moderation",
        operation_id=str(uuid.uuid4()),
    )
    assert case.status is ModerationStatus.OPEN
    with pytest.raises(ResultReleaseError, match="blocked by moderation"):
        ResultReleaseService.release(
            session, context, scenario.version.id, operation_id=str(uuid.uuid4())
        )
    assert scenario.version.status is ResultVersionStatus.APPROVED
    assert list(session.scalars(select(ResultRelease))) == []


def test_legacy_official_certificate_scope_is_denied_even_when_issuance_enabled(
    session, monkeypatch
):
    """Legacy-shaped policy inputs do not grant authority or issue any artifact."""
    scenario = build_governed_result_correction_scenario(session)
    monkeypatch.setattr(
        "app.result_release.service.get_settings",
        lambda: Settings(_env_file=None, environment="test", certification_issuance_enabled=True),
    )
    now = datetime.now(timezone.utc)

    def policy_inputs(classification):
        # Deliberately unpersisted inputs isolate the independent policy guard;
        # production authorization cannot create an OFFICIAL scope.
        release = ResultRelease(
            organisation_id=scenario.organisation.id,
            standard_pack_version_id=scenario.standard_pack_version.id,
            classification=classification,
        )
        authorization = CertificationAuthorization(
            organisation_id=scenario.organisation.id,
            standard_pack_version_id=scenario.standard_pack_version.id,
            provider_scope=scenario.standard_pack_version.standard_pack.code,
            classification=classification,
            assessment_scope=classification,
            status=CertificationAuthorizationStatus.ACTIVE,
            effective_from=now - timedelta(minutes=1),
            effective_until=now + timedelta(days=1),
        )
        return release, authorization

    practice = policy_inputs(AssessmentClassification.PRACTICE.value)
    assert ResultReleaseService._authorization_valid(session, *practice) is True
    official = policy_inputs(AssessmentClassification.OFFICIAL_CERTIFICATION.value)
    assert ResultReleaseService._authorization_valid(session, *official) is False
    assert list(session.scalars(select(CertificationAuthorization))) == []
