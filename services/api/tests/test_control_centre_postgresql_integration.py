"""Real JWT, RBAC and safe projections on the Alembic PostgreSQL schema."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api import auth_settings_dependency
from app.auth.service import AuthenticationService
from app.config import Settings, get_settings
from app.db import get_db_session
from app.main import app
from app.models import (
    AIModelProfile,
    AuditEvent,
    KnowledgeAuthority,
    KnowledgeDocumentPage,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    KnowledgeSource,
    OrganisationMembership,
    Permission,
    PlatformRoleAssignment,
    Role,
    RolePermission,
    User,
)
from app.models.enums import (
    ActorType,
    AIArtifactStatus,
    AssessmentFamily,
    ExtractionStatus,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeIngestionStatus,
    KnowledgeScope,
    KnowledgeVersionStatus,
    PlatformAssignmentStatus,
    RoleScope,
)
from app.scripts.seed_demo import PASSWORD, PLATFORM_ADMIN_EMAIL
from app.services.identity import IdentityService
from tests.alembic_helpers import repository_alembic_head
from tests.integration_support.result_correction_scenario import (
    build_governed_result_correction_scenario,
)
from tests.test_control_centre_seed import assert_platform_demo_seed

PREFIX = "/api/v1/platform/control-center"
VIEWS = (
    "/overview",
    "/organisations",
    "/standards?kind=versions",
    "/standards?kind=packs",
    "/standards?kind=authorities",
    "/standards?kind=bindings",
    "/source-registry?kind=sources",
    "/source-registry?kind=documents",
    "/source-registry?kind=ingestions",
    "/question-bank",
    "/exams",
    "/audit",
    "/ai-operations?kind=models",
    "/ai-operations?kind=workflows",
    "/ai-operations?kind=runs",
    "/ai-operations?kind=reviews",
    "/ai-operations?kind=checkpoints",
    "/system",
    "/certification?kind=authorizations",
    "/certification?kind=certificates",
)
REQUIRE_POSTGRES_INTEGRATION = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRE_POSTGRES_INTEGRATION,
    reason="POSTGRES_INTEGRATION_URL is required for Control Centre acceptance",
)


def _url() -> str:
    configured = os.getenv("POSTGRES_INTEGRATION_URL")
    if not configured:
        pytest.fail("PRUFAI_REQUIRE_POSTGRES_INTEGRATION=1 requires POSTGRES_INTEGRATION_URL")
    return configured


@dataclass
class ControlCentreScenario:
    session: Session
    client: TestClient
    settings: Settings
    identifiers: dict[str, str]

    def login(self, email: str, *, password: str = PASSWORD) -> dict[str, str]:
        response = self.client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert response.status_code == 200, response.text
        assert response.json()["token_type"] == "bearer"
        return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def control_centre() -> Iterator[ControlCentreScenario]:
    engine = create_engine(_url())
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_rate_limit_backend="memory",
        auth_jwt_secret="control-centre-postgresql-test-secret-" + "x" * 40,
    )
    original_overrides = dict(app.dependency_overrides)

    def database() -> Iterator[Session]:
        with factory() as request_session:
            yield request_session

    with factory() as session:
        identifiers = assert_platform_demo_seed(session)
        app.dependency_overrides[get_db_session] = database
        app.dependency_overrides[auth_settings_dependency] = lambda: settings
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            with TestClient(app) as client:
                yield ControlCentreScenario(session, client, settings, identifiers)
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original_overrides)
    engine.dispose()


def test_control_centre_database_is_migration_backed() -> None:
    with psycopg.connect(_url().replace("postgresql+psycopg://", "postgresql://")) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [
            (repository_alembic_head(),)
        ]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
        }
        assert {"platform_role_assignments", "audit_events", "result_releases"} <= tables


def test_platform_demo_login_me_and_all_governance_views_use_real_permissions(
    control_centre: ControlCentreScenario,
) -> None:
    scenario = control_centre
    headers = scenario.login(PLATFORM_ADMIN_EMAIL)
    me = scenario.client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    identity = me.json()
    assert identity["id"] == scenario.identifiers["platform_admin_user_id"]
    assert identity["memberships"] == []
    assert identity["platform_scope"] is True
    assert "platform.control_center.read" in identity["platform_permissions"]
    assert (
        scenario.session.scalar(
            select(OrganisationMembership.id).where(
                OrganisationMembership.user_id == uuid.UUID(identity["id"])
            )
        )
        is None
    )
    for view in VIEWS:
        response = scenario.client.get(PREFIX + view, headers=headers)
        assert response.status_code == 200, (view, response.text)
        if view not in {"/overview", "/system"}:
            payload = response.json()
            assert set(payload) >= {"items", "total", "offset", "limit"}
            assert isinstance(payload["items"], list)
            assert payload["total"] >= len(payload["items"])


@pytest.mark.parametrize(
    "email",
    ["candidate@demo.prufai.local", "reviewer@demo.prufai.local", "admin@demo.prufai.local"],
)
def test_tenant_accounts_and_forged_headers_cannot_enter_any_platform_view(
    control_centre: ControlCentreScenario, email: str
) -> None:
    scenario = control_centre
    authenticated = scenario.login(email)
    me = scenario.client.get("/api/v1/auth/me", headers=authenticated).json()
    assert me["platform_scope"] is False
    assert me["platform_permissions"] == []
    for extra in (
        {},
        {
            "X-PrufAI-Organisation-ID": scenario.identifiers["organisation_id"],
            "X-PrufAI-Platform-Admin": "true",
            "X-PrufAI-Role": "PRUFAI_SUPER_ADMIN",
            "X-PrufAI-User-ID": scenario.identifiers["platform_admin_user_id"],
            "X-PrufAI-Permissions": "platform.control_center.read,platform.audit.read",
        },
    ):
        for view in VIEWS:
            response = scenario.client.get(PREFIX + view, headers={**authenticated, **extra})
            assert response.status_code == 403, (email, view, response.text)
            assert "items" not in response.json()


def test_unauthenticated_and_invalid_token_requests_are_denied(
    control_centre: ControlCentreScenario,
) -> None:
    for view in VIEWS:
        for headers in ({}, {"Authorization": "Bearer invalid-platform-token"}):
            response = control_centre.client.get(PREFIX + view, headers=headers)
            assert response.status_code == 401, (view, response.text)


def test_platform_organisation_header_does_not_impersonate_a_tenant(
    control_centre: ControlCentreScenario,
) -> None:
    scenario = control_centre
    headers = {
        **scenario.login(PLATFORM_ADMIN_EMAIL),
        "X-PrufAI-Organisation-ID": scenario.identifiers["organisation_id"],
    }
    for view in VIEWS:
        response = scenario.client.get(PREFIX + view, headers=headers)
        assert response.status_code == 400, (view, response.text)


def test_explicit_tenant_metadata_filter_is_scoped_and_requires_extra_platform_permission(
    control_centre: ControlCentreScenario,
) -> None:
    scenario = control_centre
    other = build_governed_result_correction_scenario(scenario.session)
    scenario.session.commit()
    headers = scenario.login(PLATFORM_ADMIN_EMAIL)
    for view in ("/question-bank", "/exams"):
        shared = scenario.client.get(PREFIX + view, headers=headers)
        assert shared.status_code == 200
        assert all(row["scope_type"] == "PLATFORM_SHARED" for row in shared.json()["items"])
        first = scenario.client.get(
            PREFIX + view,
            params={"organisation_id": scenario.identifiers["organisation_id"], "limit": 100},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        assert first.json()["items"]
        assert all(
            row["organisation_id"] == scenario.identifiers["organisation_id"]
            for row in first.json()["items"]
        )
        second = scenario.client.get(
            PREFIX + view,
            params={"organisation_id": str(other.organisation.id), "limit": 100},
            headers=headers,
        )
        assert second.status_code == 200, second.text
        assert second.json()["items"]
        assert all(
            row["organisation_id"] == str(other.organisation.id) for row in second.json()["items"]
        )
        assert {row["id"] for row in first.json()["items"]}.isdisjoint(
            row["id"] for row in second.json()["items"]
        )
        private_text = json.dumps(second.json())
        assert other.candidate.email not in private_text
        assert other.second_candidate.email not in private_text
        assert "correct_option_ids" not in private_text
        assert "transcript" not in private_text
        assert "current_payload" not in private_text
    operator, password = _platform_actor(
        scenario, {"platform.control_center.read", "question_bank.read", "exam.read"}
    )
    limited_headers = scenario.login(operator.email, password=password)
    for view in ("/question-bank", "/exams"):
        assert scenario.client.get(PREFIX + view, headers=limited_headers).status_code == 200
        assert (
            scenario.client.get(
                PREFIX + view,
                params={"organisation_id": scenario.identifiers["organisation_id"]},
                headers=limited_headers,
            ).status_code
            == 403
        )


def _platform_actor(scenario: ControlCentreScenario, permissions: set[str]) -> tuple[User, str]:
    code = uuid.uuid4().hex
    user = IdentityService.create_user(scenario.session, email=f"operator-{code}@fixture.invalid")
    password = f"Synthetic-operator-test-{code}"
    AuthenticationService(scenario.settings).set_password(scenario.session, user, password)
    role = Role(key=f"CONTROL_TEST_{code}", scope=RoleScope.PLATFORM)
    scenario.session.add(role)
    scenario.session.flush()
    for key in permissions:
        permission = scenario.session.scalar(select(Permission).where(Permission.key == key))
        assert permission is not None
        scenario.session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    scenario.session.add(PlatformRoleAssignment(user_id=user.id, role_id=role.id))
    scenario.session.commit()
    return user, password


def test_platform_baseline_and_domain_permissions_are_both_required(
    control_centre: ControlCentreScenario,
) -> None:
    scenario = control_centre
    for permissions in ({"platform.control_center.read"}, {"platform.organisation.read"}):
        user, password = _platform_actor(scenario, permissions)
        headers = scenario.login(user.email, password=password)
        for view in VIEWS:
            response = scenario.client.get(PREFIX + view, headers=headers)
            expected = (
                200
                if view == "/overview" and "platform.control_center.read" in permissions
                else 403
            )
            assert response.status_code == expected, (permissions, view, response.text)


def test_suspending_platform_role_revokes_access_for_an_existing_jwt(
    control_centre: ControlCentreScenario,
) -> None:
    scenario = control_centre
    user, password = _platform_actor(scenario, {"platform.control_center.read"})
    headers = scenario.login(user.email, password=password)
    assert scenario.client.get(PREFIX + "/overview", headers=headers).status_code == 200
    assignment = scenario.session.scalar(
        select(PlatformRoleAssignment).where(PlatformRoleAssignment.user_id == user.id)
    )
    assert assignment is not None
    assignment.status = PlatformAssignmentStatus.SUSPENDED
    scenario.session.commit()
    assert scenario.client.get(PREFIX + "/overview", headers=headers).status_code == 403


def test_safe_metadata_omits_private_audit_payloads_source_text_and_ai_configuration(
    control_centre: ControlCentreScenario,
) -> None:
    scenario = control_centre
    marker = f"PRIVATE_CONTROL_CENTRE_SENTINEL_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    audit = AuditEvent(
        timestamp=now,
        actor_type=ActorType.HUMAN,
        actor_id=uuid.UUID(scenario.identifiers["platform_admin_user_id"]),
        organisation_id=uuid.UUID(scenario.identifiers["organisation_id"]),
        event_type="candidate.synthetic_privacy_probe",
        entity_type="candidate_response",
        entity_id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        reason=marker,
        metadata_json={
            "password": marker,
            "token": marker,
            "candidate_answer": marker,
            "reviewer_private_notes": marker,
            "prompt": marker,
            "answer_key": marker,
            "audio_bytes": marker,
            "transcript": marker,
            "storage_key": marker,
            "private_url": marker,
            "operation_id": str(uuid.uuid4()),
        },
    )
    hostile_reference = AuditEvent(
        timestamp=now,
        actor_type=ActorType.HUMAN,
        actor_id=audit.actor_id,
        organisation_id=audit.organisation_id,
        event_type=marker,
        entity_type=marker,
        entity_id=marker,
        request_id=marker,
        workflow_id=marker,
        metadata_json={"operation_id": marker},
    )
    # Legacy raw audit rows are deliberate hostile read-side fixtures. This
    # does not bypass the application writer for a production operation.
    authority = KnowledgeAuthority(
        code=f"CONTROL_TEST_{uuid.uuid4().hex}",
        name="Synthetic Control Centre privacy authority",
        authority_type=KnowledgeAuthorityType.INTERNAL,
    )
    model = AIModelProfile(
        code=f"CONTROL_TEST_{uuid.uuid4().hex}",
        name="Synthetic unconfigured model",
        provider="SYNTHETIC_TEST",
        model_name="not-connected",
        purpose="metadata privacy test",
        status=AIArtifactStatus.DRAFT,
        configuration={"api_key": marker, "system_prompt": marker},
    )
    scenario.session.add_all([audit, hostile_reference, authority, model])
    scenario.session.flush()
    source = KnowledgeSource(
        authority_id=authority.id,
        source_code=f"CONTROL_TEST_{uuid.uuid4().hex}",
        title="Synthetic LiD source metadata",
        document_type=KnowledgeDocumentType.POLICY,
        scope=KnowledgeScope.PLATFORM_SHARED,
        assessment_family=AssessmentFamily.INTEGRATION_KNOWLEDGE,
        exam_family="LiD",
        cefr_level=None,
        notes=marker,
    )
    scenario.session.add(source)
    scenario.session.flush()
    document = KnowledgeDocumentVersion(
        source_id=source.id,
        version_label="synthetic-privacy",
        status=KnowledgeVersionStatus.DRAFT,
        storage_key=f"private/{marker}",
        source_url=f"https://private.invalid/{marker}",
        notes=marker,
        created_at=now,
    )
    scenario.session.add(document)
    scenario.session.flush()
    ingestion = KnowledgeIngestionJob(
        document_version_id=document.id,
        status=KnowledgeIngestionStatus.FAILED,
        parser_name="synthetic-privacy-fixture",
        parser_version="1",
        error_code="SYNTHETIC_PRIVACY_FIXTURE",
        error_message_safe=marker,
        created_by_user_id=uuid.UUID(scenario.identifiers["platform_admin_user_id"]),
        created_at=now,
    )
    scenario.session.add(ingestion)
    scenario.session.flush()
    page = KnowledgeDocumentPage(
        document_version_id=document.id,
        ingestion_job_id=ingestion.id,
        page_number=1,
        text_content=marker,
        normalized_text=marker,
        content_hash=hashlib.sha256(marker.encode()).hexdigest(),
        character_count=len(marker),
        extraction_status=ExtractionStatus.EXTRACTED,
        parser_name="synthetic-privacy-fixture",
        parser_version="1",
        created_at=now,
    )
    scenario.session.add(page)
    scenario.session.commit()
    audit_before = scenario.session.scalar(select(func.count()).select_from(AuditEvent))
    headers = scenario.login(PLATFORM_ADMIN_EMAIL)
    for view in VIEWS:
        response = scenario.client.get(PREFIX + view, headers=headers)
        assert response.status_code == 200, (view, response.text)
        serialized = json.dumps(response.json())
        assert marker not in serialized, view
        for email in (
            "candidate@demo.prufai.local",
            "history@demo.prufai.local",
            "reviewer@demo.prufai.local",
        ):
            assert email not in serialized, view
    for event in (audit, hostile_reference):
        filtered = scenario.client.get(
            PREFIX + "/audit",
            params={
                "event_type": event.event_type,
                "organisation_id": str(audit.organisation_id),
            },
            headers=headers,
        )
        assert filtered.status_code == 200, filtered.text
        item = next(row for row in filtered.json()["items"] if row["id"] == str(event.id))
        assert "metadata" not in item and "metadata_json" not in item and "reason" not in item
        assert marker not in json.dumps(item)
        if event is hostile_reference:
            assert item["event_type"] == "REDACTED"
            assert item["entity_type"] == "REDACTED"
            for key in ("entity_id", "operation_id", "request_id", "workflow_id"):
                assert item[key] is None
        else:
            assert item["operation_id"] == audit.metadata_json["operation_id"]
    source_rows = scenario.client.get(
        PREFIX + "/source-registry?kind=sources&limit=100", headers=headers
    ).json()["items"]
    source_item = next(row for row in source_rows if row["id"] == str(source.id))
    assert source_item["assessment_family"] == "INTEGRATION_KNOWLEDGE"
    assert source_item["cefr_level"] is None
    # Authentication appends its normal login audit; reads preserve old evidence.
    scenario.session.expire_all()
    assert scenario.session.get(AuditEvent, audit.id).metadata_json["candidate_answer"] == marker
    assert scenario.session.get(KnowledgeDocumentVersion, document.id).storage_key.endswith(marker)
    assert scenario.session.get(KnowledgeDocumentPage, page.id).text_content == marker
    assert scenario.session.scalar(select(func.count()).select_from(AuditEvent)) >= audit_before


@pytest.mark.parametrize("query", ["offset=-1", "limit=0", "limit=101"])
def test_platform_pagination_is_bounded(control_centre: ControlCentreScenario, query: str) -> None:
    headers = control_centre.login(PLATFORM_ADMIN_EMAIL)
    response = control_centre.client.get(PREFIX + "/audit?" + query, headers=headers)
    assert response.status_code == 422
