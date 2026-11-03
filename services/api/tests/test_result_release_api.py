"""HTTP contract/security regressions; real PostgreSQL proof lives separately."""

import uuid
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, routing
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.api import Principal, get_principal
from app.config import Settings
from app.db import get_db_session
from app.main import app
from app.models import AuditEvent, CertificationAuthorization, ResultRelease, StatementOfResults
from app.models.enums import ResultVersionStatus
from app.result_release.service import ResultReleaseService
from tests.integration_support.result_release_scenario import build_governed_release_scenario


@pytest.fixture
def release_api(session):
    scenario = build_governed_release_scenario(session)
    context = scenario.context
    session.commit()

    def db():
        yield session

    def authenticate(selected_context=context, user=None):
        principal = Principal(
            user=user or scenario.historical.reviewer,
            session=SimpleNamespace(id=uuid.uuid4()),
            tenant_context=selected_context,
        )
        app.dependency_overrides[get_principal] = lambda: principal

    app.dependency_overrides[get_db_session] = db
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, session, scenario, context, authenticate
    finally:
        client.close()
        app.dependency_overrides.clear()


def registered_api_routes(application):
    # FastAPI >=0.162 keeps includes lazy; its public iterator supplies their
    # effective prefixes while preserving the underlying APIRoute identity.
    iter_contexts = getattr(routing, "iter_route_contexts", None)
    if iter_contexts is not None:
        routes = [
            context
            for context in iter_contexts(application.routes)
            if isinstance(context.original_route, APIRoute)
        ]
    else:
        routes = [route for route in application.routes if isinstance(route, APIRoute)]
    assert routes, "no effective API routes were inspected"
    return routes


def assert_unique_method_path_bindings(application):
    routes = registered_api_routes(application)
    bindings = [(method, route.path) for route in routes for method in route.methods]
    duplicates = {binding: count for binding, count in Counter(bindings).items() if count > 1}
    assert duplicates == {}, f"duplicate HTTP route bindings: {duplicates}"
    return routes


def test_http_routes_have_one_authoritative_method_path_binding():
    routes = assert_unique_method_path_bindings(app)
    release_routes = [
        route
        for route in routes
        if route.path
        == "/api/v1/organisations/{organisation_id}/result-versions/{version_id}/release"
        and "POST" in route.methods
    ]
    assert len(release_routes) == 1
    assert release_routes[0].endpoint.__module__ == "app.scoring.api"
    assert release_routes[0].endpoint.__name__ == "release_result"


def test_route_inspection_detects_duplicate_nested_includes_and_respects_prefixes():
    leaf = APIRouter()

    @leaf.post("/release")
    def release_probe():
        return {"synthetic_route_probe": True}

    parent = APIRouter()
    parent.include_router(leaf, prefix="/v1")
    application = FastAPI()
    application.include_router(parent, prefix="/api")
    application.include_router(parent, prefix="/another")
    routes = assert_unique_method_path_bindings(application)
    assert {(route.path, tuple(sorted(route.methods))) for route in routes} == {
        ("/api/v1/release", ("POST",)),
        ("/another/v1/release", ("POST",)),
    }
    assert all(route.endpoint is release_probe for route in routes)
    application.include_router(parent, prefix="/api")
    with pytest.raises(AssertionError, match="duplicate HTTP route bindings.*api/v1/release"):
        assert_unique_method_path_bindings(application)


def test_release_http_handler_records_and_replays_authoritative_release(release_api):
    client, session, scenario, context, authenticate = release_api
    authenticate()
    path = (
        f"/api/v1/organisations/{context.organisation_id}/result-versions/"
        f"{scenario.version.id}/release"
    )
    assert client.post(path, json={}).status_code == 422
    assert scenario.version.status is ResultVersionStatus.APPROVED
    operation_id = str(uuid.uuid4())
    response = client.post(path, json={"operation_id": operation_id})
    assert response.status_code == 200, response.text
    release = session.scalar(select(ResultRelease))
    assert release is not None
    assert response.json()["id"] == str(scenario.version.id)
    assert response.json()["release_id"] == str(release.id)
    assert release.operation_id == operation_id
    assert scenario.version.status is ResultVersionStatus.RELEASED
    assert release.scoring_run_id == scenario.run.id
    assert release.candidate_user_id == scenario.candidate_context.user_id
    replay = client.post(path, json={"operation_id": operation_id})
    assert replay.status_code == 200
    assert replay.json() == response.json()
    assert len(list(session.scalars(select(ResultRelease)))) == 1


def test_release_endpoints_require_authentication(release_api):
    client, _, scenario, context, _ = release_api
    organisation = f"/api/v1/organisations/{context.organisation_id}"
    requests = (
        ("GET", f"{organisation}/releases", None),
        (
            "POST",
            f"{organisation}/result-versions/{scenario.version.id}/release",
            {"operation_id": str(uuid.uuid4())},
        ),
        (
            "POST",
            f"{organisation}/releases/{uuid.uuid4()}/statement",
            {"operation_id": str(uuid.uuid4())},
        ),
        ("GET", "/api/v1/candidate/releases", None),
        ("GET", f"/api/v1/candidate/releases/{uuid.uuid4()}/statement", None),
    )
    for method, path, payload in requests:
        assert client.request(method, path, json=payload).status_code == 401


def test_release_http_denies_read_only_and_unscoped_platform_callers(release_api):
    client, session, scenario, context, authenticate = release_api
    path = (
        f"/api/v1/organisations/{context.organisation_id}/result-versions/"
        f"{scenario.version.id}/release"
    )
    authenticate(replace(context, permissions=frozenset({"result.read"})))
    assert client.post(path, json={"operation_id": str(uuid.uuid4())}).status_code == 403
    authenticate(
        replace(
            context,
            organisation_id=None,
            permissions=frozenset(),
            platform_permissions=frozenset({"result.release"}),
        )
    )
    assert client.post(path, json={"operation_id": str(uuid.uuid4())}).status_code == 403
    assert list(session.scalars(select(ResultRelease))) == []


def test_http_statement_issuance_checks_permission_even_on_replay(release_api):
    client, session, scenario, context, authenticate = release_api
    release = ResultReleaseService.release(
        session, context, scenario.version.id, operation_id=str(uuid.uuid4())
    )
    operation_id = str(uuid.uuid4())
    statement = ResultReleaseService.statement(
        session, context, release.id, operation_id=operation_id
    )
    session.commit()
    authenticate(
        replace(
            context,
            user_id=scenario.historical.candidate.id,
            permissions=frozenset({"result.read"}),
        ),
        scenario.historical.candidate,
    )
    path = f"/api/v1/organisations/{context.organisation_id}/releases/{release.id}/statement"
    for requested_operation in (operation_id, str(uuid.uuid4())):
        response = client.post(path, json={"operation_id": requested_operation})
        assert response.status_code == 403, response.text
        assert "payload" not in response.json()
    assert list(session.scalars(select(StatementOfResults))) == [statement]


def test_candidate_statement_reads_are_owner_scoped(release_api):
    client, session, scenario, context, authenticate = release_api
    release = ResultReleaseService.release(
        session, context, scenario.version.id, operation_id=str(uuid.uuid4())
    )
    statement = ResultReleaseService.statement(
        session, context, release.id, operation_id=str(uuid.uuid4())
    )
    session.commit()
    candidate = replace(
        context, user_id=scenario.historical.second_candidate.id, permissions=frozenset()
    )
    authenticate(candidate, scenario.historical.second_candidate)
    path = f"/api/v1/candidate/releases/{release.id}/statement"
    own = client.get(path)
    assert own.status_code == 200, own.text
    assert own.json()["id"] == str(statement.id)
    authenticate(
        replace(candidate, user_id=scenario.historical.candidate.id), scenario.historical.candidate
    )
    denied = client.get(path)
    assert denied.status_code == 404, denied.text
    assert "payload" not in denied.json()
    rows = client.get("/api/v1/candidate/releases")
    assert rows.status_code == 200
    assert rows.json() == []
    authenticate(
        replace(candidate, organisation_id=uuid.uuid4()), scenario.historical.second_candidate
    )
    denied = client.get(path)
    assert denied.status_code in {403, 404}, denied.text
    assert "payload" not in denied.json()


def test_http_certification_authorization_requires_scoped_platform_permission(
    release_api, monkeypatch
):
    client, session, scenario, context, authenticate = release_api
    monkeypatch.setattr(
        "app.result_release.service.get_settings",
        lambda: Settings(_env_file=None, environment="test", certification_issuance_enabled=True),
    )
    path = f"/api/v1/organisations/{context.organisation_id}/certification-authorizations"
    payload = {
        "assessment_scope": scenario.exam_version.classification.value,
        "provider_scope": scenario.historical.standard_pack_version.standard_pack.code,
        "standard_pack_version_id": str(scenario.historical.standard_pack_version.id),
        "operation_id": str(uuid.uuid4()),
        "evidence_reference": "SYNTHETIC TEST AUTHORIZATION - NOT OFFICIAL MATERIAL",
        "effective_from": datetime.now(timezone.utc).isoformat(),
    }
    authenticate(context)
    institution = client.post(path, json=payload)
    assert institution.status_code == 403, institution.text
    authenticate(
        replace(
            context,
            organisation_id=None,
            permissions=frozenset(),
            platform_permissions=frozenset({"certification.authorize"}),
        )
    )
    unscoped_platform = client.post(path, json=payload)
    assert unscoped_platform.status_code == 403, unscoped_platform.text
    assert list(session.scalars(select(CertificationAuthorization))) == []


def test_http_revocation_preserves_owner_history_and_invalidates_statement(release_api):
    client, session, scenario, context, authenticate = release_api
    authenticate()
    organisation = f"/api/v1/organisations/{context.organisation_id}"
    released = client.post(
        f"{organisation}/result-versions/{scenario.version.id}/release",
        json={"operation_id": str(uuid.uuid4())},
    )
    assert released.status_code == 200, released.text
    release_id = released.json()["release_id"]
    operation_id = str(uuid.uuid4())
    statement_path = f"{organisation}/releases/{release_id}/statement"
    issued = client.post(statement_path, json={"operation_id": operation_id})
    assert issued.status_code == 200, issued.text
    original = issued.json()
    assert original["effective_status"] == "ISSUED"
    assert original["currently_valid"] is True
    revoke_path = f"{organisation}/releases/{release_id}/revoke"
    revoke_operation = str(uuid.uuid4())
    revoke_payload = {
        "reason": "Synthetic HTTP lifecycle regression",
        "operation_id": revoke_operation,
    }
    assert client.post(revoke_path, json={"reason": revoke_payload["reason"]}).status_code == 422
    revoked = client.post(revoke_path, json=revoke_payload)
    assert revoked.status_code == 200, revoked.text
    replayed = client.post(revoke_path, json=revoke_payload)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == revoked.json()
    for changed in (
        {**revoke_payload, "operation_id": str(uuid.uuid4())},
        {**revoke_payload, "reason": "Different synthetic reason"},
    ):
        conflict = client.post(revoke_path, json=changed)
        assert conflict.status_code == 409, conflict.text
    revocation_events = list(
        session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == release_id,
                AuditEvent.event_type == "result.release_revoked",
            )
        )
    )
    assert len(revocation_events) == 1
    assert revocation_events[0].metadata_json["operation_id"] == revoke_operation
    assert revocation_events[0].reason == revoke_payload["reason"]
    for requested_operation in (operation_id, str(uuid.uuid4())):
        denied = client.post(statement_path, json={"operation_id": requested_operation})
        assert denied.status_code == 409, denied.text
    authenticate(scenario.candidate_context, scenario.historical.second_candidate)
    listing = client.get("/api/v1/candidate/releases")
    assert listing.status_code == 200, listing.text
    assert [(row["id"], row["effective_status"]) for row in listing.json()] == [
        (release_id, "REVOKED")
    ]
    detail = client.get(f"/api/v1/candidate/releases/{release_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["effective_status"] == "REVOKED"
    historical = client.get(f"/api/v1/candidate/releases/{release_id}/statement")
    assert historical.status_code == 200, historical.text
    payload = historical.json()
    assert payload["id"] == original["id"]
    assert payload["checksum"] == original["checksum"]
    assert payload["payload"] == original["payload"]
    assert payload["status"] == "ISSUED"
    assert payload["effective_status"] == "REVOKED"
    assert payload["currently_valid"] is False
    assert detail.json()["statements"] == [payload]
    assert len(list(session.scalars(select(StatementOfResults)))) == 1
