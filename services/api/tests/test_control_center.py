"""Unit/API projections and fail-closed platform RBAC; real PostgreSQL is separate."""

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.audit import AuditService
from app.auth.api import Principal, get_principal
from app.authorization import TenantContext
from app.authorization.seed import PLATFORM_PERMISSIONS
from app.config import Settings, get_settings
from app.control_center.repository import ControlCenterRepository
from app.control_center.service import ControlCenterService
from app.db import get_db_session
from app.main import app
from app.models import AIModelProfile, AuditEvent
from app.models.enums import AIArtifactStatus
from app.result_release.service import ResultReleaseService
from app.scoring.service import ScoringService
from tests.integration_support.result_correction_scenario import (
    build_governed_result_correction_scenario,
)
from tests.integration_support.result_release_scenario import build_governed_release_scenario

PREFIX = "/api/v1/platform/control-center"
PAGES = (
    "/organisations",
    "/standards",
    "/standards?kind=packs",
    "/standards?kind=authorities",
    "/standards?kind=bindings",
    "/source-registry",
    "/source-registry?kind=documents",
    "/source-registry?kind=ingestions",
    "/question-bank",
    "/exams",
    "/audit",
    "/ai-operations",
    "/ai-operations?kind=workflows",
    "/ai-operations?kind=runs",
    "/ai-operations?kind=reviews",
    "/ai-operations?kind=checkpoints",
    "/certification",
    "/certification?kind=certificates",
)
ALL_PATHS = (*PAGES, "/overview", "/system")
PRIVATE = "PRIVATE_PROMPT_PASSWORD_CANDIDATE_RESPONSE_DO_NOT_EXPOSE"


@pytest.fixture
def control_api(session):
    scenario = build_governed_result_correction_scenario(session)
    session.commit()
    platform = TenantContext(
        user_id=scenario.reviewer.id,
        organisation_id=None,
        membership_id=None,
        platform_permissions=frozenset(PLATFORM_PERMISSIONS),
    )

    def db():
        yield session

    def authenticate(context=platform):
        principal = Principal(
            user=scenario.reviewer, session=SimpleNamespace(id=uuid.uuid4()), tenant_context=context
        )
        app.dependency_overrides[get_principal] = lambda: principal

    app.dependency_overrides[get_db_session] = db
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, session, scenario, platform, authenticate
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_control_center_requires_authentication_and_platform_capabilities(control_api):
    client, _, scenario, platform, authenticate = control_api
    for path in ALL_PATHS:
        assert client.get(PREFIX + path).status_code == 401
    tenant = replace(
        platform,
        organisation_id=scenario.organisation.id,
        permissions=frozenset(PLATFORM_PERMISSIONS),
        platform_permissions=frozenset(),
    )
    authenticate(tenant)
    for path in ALL_PATHS:
        assert client.get(PREFIX + path).status_code == 403
        assert (
            client.get(
                PREFIX + path, headers={"X-PrufAI-Organisation-ID": str(scenario.organisation.id)}
            ).status_code
            == 403
        )
    authenticate(
        replace(platform, platform_permissions=frozenset({"platform.control_center.read"}))
    )
    for path in (*PAGES, "/system"):
        assert client.get(PREFIX + path).status_code == 403


def test_platform_metadata_endpoints_are_paginated_read_only_and_safe(control_api):
    client, session, scenario, _, authenticate = control_api
    authenticate()
    for path in PAGES:
        response = client.get(PREFIX + path)
        assert response.status_code == 200, (path, response.text)
        payload = response.json()
        assert set(payload) == {"items", "total", "offset", "limit"}
        assert payload["offset"] == 0 and payload["limit"] == 25
        assert len(payload["items"]) <= 25
        assert payload["total"] >= len(payload["items"])
        assert "candidate_user_id" not in response.text
        assert scenario.candidate.normalized_email not in response.text
        assert scenario.second_candidate.normalized_email not in response.text
    for path in ("/organisations", "/audit", "/system", "/certification"):
        assert client.post(PREFIX + path, json={}).status_code == 405
    assert not session.new and not session.dirty and not session.deleted


def test_overview_only_queries_and_exposes_permitted_domains(control_api):
    client, session, _, platform, authenticate = control_api
    read_only = replace(
        platform,
        platform_permissions=frozenset(
            {
                "platform.control_center.read",
                "platform.organisation.read",
            }
        ),
    )
    authenticate(read_only)
    overview = client.get(PREFIX + "/overview")
    assert overview.status_code == 200, overview.text
    payload = overview.json()
    assert payload["metrics"]["organisations"] == 1
    assert all(value is None for key, value in payload["metrics"].items() if key != "organisations")
    assert payload["available_sections"] == ["organisations"]
    assert payload["recent_audit"] is None
    assert payload["service_health"] is None
    listing = client.get(PREFIX + "/organisations").json()
    assert listing["items"][0]["membership_count"] is None
    authenticate()
    before = ControlCenterRepository.recent_audit_count(session)
    event = session.scalar(select(AuditEvent))
    if event is not None:
        # Audit rows are append-only; create an old event rather than rewriting history.
        old = AuditEvent(
            timestamp=datetime.now(timezone.utc) - timedelta(days=2),
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            organisation_id=event.organisation_id,
            event_type="auth.session.created",
            entity_type="auth_session",
            entity_id=str(uuid.uuid4()),
        )
        session.add(old)
        session.flush()
    payload = client.get(PREFIX + "/overview").json()
    assert payload["metrics"]["recent_audit_events"] == before
    assert payload["metric_scopes"]["recent_audit_events"] == "LAST_24_HOURS"
    assert len(payload["recent_audit"]) <= 5
    assert payload["service_health"]["database"]["status"] == "reachable"


def test_reference_filter_does_not_impersonate_organisation_and_requires_capability(control_api):
    client, _, scenario, platform, authenticate = control_api
    authenticate()
    for domain in ("question-bank", "exams"):
        shared = client.get(f"{PREFIX}/{domain}")
        assert shared.status_code == 200
        assert all(row["scope_type"] == "PLATFORM_SHARED" for row in shared.json()["items"])
        scoped = client.get(f"{PREFIX}/{domain}?organisation_id={scenario.organisation.id}")
        assert scoped.status_code == 200, scoped.text
        assert scoped.json()["items"]
        assert all(
            row["organisation_id"] == str(scenario.organisation.id)
            for row in scoped.json()["items"]
        )
        assert client.get(f"{PREFIX}/{domain}?organisation_id={uuid.uuid4()}").json()["items"] == []
        authenticate(
            replace(
                platform,
                platform_permissions=platform.platform_permissions - {"platform.organisation.read"},
            )
        )
        assert (
            client.get(f"{PREFIX}/{domain}?organisation_id={scenario.organisation.id}").status_code
            == 403
        )
        authenticate()
    forged = client.get(
        PREFIX + "/overview", headers={"X-PrufAI-Organisation-ID": str(scenario.organisation.id)}
    )
    assert forged.status_code == 400


def test_audit_projection_is_allowlisted_and_filters_persisted_metadata(control_api):
    client, session, scenario, platform, authenticate = control_api
    operation = str(uuid.uuid4())
    AuditService.record(
        session,
        replace(platform, organisation_id=scenario.organisation.id),
        event_type="candidate_audio.accessed",
        entity_type="CANDIDATE_AUDIO",
        entity_id=str(uuid.uuid4()),
        organisation_id=scenario.organisation.id,
        reason=PRIVATE,
        request_id=PRIVATE,
        workflow_id=PRIVATE,
        metadata={
            "operation_id": operation,
            "source_version_id": PRIVATE,
            "candidate_response": PRIVATE,
            "audio_bytes": PRIVATE,
            "storage_key": PRIVATE,
            "private_note": PRIVATE,
        },
        require_audit_permission=False,
    )
    session.flush()
    authenticate()
    response = client.get(
        f"{PREFIX}/audit?event_type=candidate_audio.accessed&organisation_id={scenario.organisation.id}"
    )
    assert response.status_code == 200, response.text
    assert PRIVATE not in response.text
    row = response.json()["items"][0]
    assert row["operation_id"] == operation
    assert row["request_id"] is None and row["workflow_id"] is None
    assert set(row) == {
        "id",
        "timestamp",
        "actor_type",
        "actor_id",
        "organisation_id",
        "event_type",
        "entity_type",
        "entity_id",
        "operation_id",
        "request_id",
        "workflow_id",
    }
    assert client.get(f"{PREFIX}/audit?organisation_id={uuid.uuid4()}").json()["items"] == []


def test_ai_and_system_metadata_do_not_claim_live_execution_or_expose_secrets(
    control_api, monkeypatch
):
    client, session, _, _, authenticate = control_api
    session.add(
        AIModelProfile(
            code="synthetic-private-config",
            name="Synthetic model",
            provider="test",
            model_name="metadata-only",
            purpose="Synthetic test",
            status=AIArtifactStatus.DRAFT,
            configuration={"api_key": PRIVATE, "prompt": PRIVATE},
            supports_structured_output=True,
        )
    )
    session.flush()
    settings = Settings(
        _env_file=None,
        environment="test",
        object_storage_access_key=PRIVATE,
        object_storage_secret_key=PRIVATE,
        knowledge_embedding_api_key=PRIVATE,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    authenticate()
    models = client.get(PREFIX + "/ai-operations")
    assert models.status_code == 200 and PRIVATE not in models.text
    system = client.get(PREFIX + "/system")
    assert system.status_code == 200 and PRIVATE not in system.text
    assert system.json()["database"]["status"] == "reachable"
    assert system.json()["ai_executor"]["status"] == "UNCONFIGURED"
    assert system.json()["object_storage"]["health"] == "NOT_VERIFIED"
    assert system.json()["certification"]["official_issuance"] == "DISABLED"

    def unavailable(_):
        raise OperationalError(PRIVATE, {}, Exception(PRIVATE))

    monkeypatch.setattr(ControlCenterRepository, "database_reachable", unavailable)
    failed = client.get(PREFIX + "/system")
    assert failed.status_code == 200 and PRIVATE not in failed.text
    assert failed.json()["database"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "event_type=private%20speech",
        "event_type=" + "a" * 161,
        "organisation_id=not-a-uuid",
    ],
)
def test_audit_query_bounds_are_enforced(control_api, query):
    client, _, _, _, authenticate = control_api
    authenticate()
    assert client.get(PREFIX + "/audit?" + query).status_code == 422


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("active", "ACTIVE"),
        ("expired", "EXPIRED"),
        ("future", "INACTIVE"),
        ("revoked", "REVOKED"),
        ("disabled", "DISABLED"),
    ],
)
def test_certification_oversight_derives_current_state_without_claiming_entitlement(
    control_api, monkeypatch, state, expected
):
    client, session, scenario, platform, authenticate = control_api
    enabled = Settings(_env_file=None, environment="test", certification_issuance_enabled=True)
    monkeypatch.setattr("app.result_release.service.get_settings", lambda: enabled)
    now = datetime.now(timezone.utc)
    from_date = now - timedelta(days=2)
    until = now + timedelta(days=1)
    if state == "expired":
        until = now - timedelta(days=1)
    elif state == "future":
        from_date = now + timedelta(hours=1)
    authority_context = replace(platform, organisation_id=scenario.organisation.id)
    authorization = ResultReleaseService.authorize(
        session,
        authority_context,
        assessment_scope="PRACTICE",
        provider_scope=scenario.standard_pack_version.standard_pack.code,
        standard_pack_version_id=scenario.standard_pack_version.id,
        operation_id=str(uuid.uuid4()),
        evidence_reference="SYNTHETIC TEST AUTHORIZATION - NOT OFFICIAL MATERIAL",
        effective_from=from_date,
        effective_until=until,
    )
    if state == "revoked":
        ResultReleaseService.revoke_authorization(
            session,
            authority_context,
            authorization.id,
            "Synthetic revocation",
            operation_id=str(uuid.uuid4()),
        )
    current_settings = (
        Settings(_env_file=None, environment="test") if state == "disabled" else enabled
    )
    monkeypatch.setattr("app.control_center.service.get_settings", lambda: current_settings)
    session.flush()
    authenticate()
    response = client.get(PREFIX + "/certification")
    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    assert row["id"] == str(authorization.id)
    assert row["effective_status"] == expected
    assert row["currently_valid"] is (state == "active")
    assert row["authorization_kind"] == "SYNTHETIC_TEST"
    assert row["official_entitlement"] is False
    assert "evidence_reference" not in row
    assert "candidate_user_id" not in row


def test_audit_keeps_actual_service_entity_types_and_redacts_untrusted_strings(session):
    scenario = build_governed_release_scenario(session)
    context = replace(scenario.context, permissions=scenario.context.permissions | {"audit.read"})
    ScoringService.release(session, context, scenario.version.id)
    actual = session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "result.released",
            AuditEvent.entity_id == str(scenario.version.id),
        )
    )
    assert actual is not None and actual.entity_type == "ResultVersion"
    untrusted = AuditService.record(
        session,
        context,
        event_type="result.released",
        entity_type=PRIVATE,
        entity_id=PRIVATE,
        organisation_id=scenario.historical.organisation.id,
        metadata={"operation_id": PRIVATE},
    )
    platform = replace(
        context,
        organisation_id=None,
        permissions=frozenset(),
        platform_permissions=frozenset(PLATFORM_PERMISSIONS),
    )
    page = ControlCenterService.audit(session, platform, 0, 25, None, "result.released")
    by_id = {row["id"]: row for row in page["items"]}
    assert by_id[actual.id]["entity_type"] == "ResultVersion"
    assert by_id[actual.id]["entity_id"] == str(scenario.version.id)
    assert by_id[untrusted.id]["entity_type"] == "REDACTED"
    assert by_id[untrusted.id]["entity_id"] is None
    assert by_id[untrusted.id]["operation_id"] is None
    assert PRIVATE not in str(page)
