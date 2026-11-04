"""Platform registry API/RBAC evidence, not provider or PostgreSQL execution evidence."""

import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth.api import Principal, get_principal
from app.config import Settings, get_settings
from app.db import get_db_session
from app.main import app
from app.models import (
    AgentVersion,
    AIModelProfile,
    AIRun,
    AIWorkflowVersion,
    AuditEvent,
    HumanReviewRequest,
    PromptVersion,
)
from app.models.enums import AIArtifactStatus
from app.writing_execution.registry import APPROVAL_EVENT, snapshot_execution_profile
from tests.test_writing_execution_registry import writing_registry  # noqa: F401

PREFIX = "/api/v1/platform/ai/writing-execution-profiles"
PRIVATE_PROMPT = "PRIVATE synthetic Writing instructions; never platform metadata."
METADATA_KEYS = {
    "execution_profile_id",
    "profile_version",
    "model_profile_id",
    "model_version",
    "prompt_version_id",
    "prompt_checksum",
    "agent_version_id",
    "agent_checksum",
    "workflow_version_id",
    "workflow_checksum",
    "provider",
    "model_name",
    "adapter_version",
    "availability",
    "unavailable_reason",
    "test_only",
    "registry_approved",
    "external_processing_authorized",
}


@pytest.fixture
def profile_api(session, writing_registry):  # noqa: F811 - pytest resolves the imported fixture
    previous = dict(app.dependency_overrides)
    _, users, creator, reviewer, arguments = writing_registry

    def database():
        yield session

    def authenticate(index=0, context=None):
        actual_context = context if context is not None else (creator, reviewer)[index]
        principal = Principal(
            user=users[index],
            session=SimpleNamespace(id=uuid.uuid4()),
            tenant_context=actual_context,
        )
        app.dependency_overrides[get_principal] = lambda: principal

    payload = {key: value for key, value in arguments.items() if key != "context"} | {
        "code": "writing-api-contract",
        "name": "Synthetic Writing API contract",
        "system_template": PRIVATE_PROMPT,
        "instruction_template": "Private synthetic rubric-bound instructions.",
    }
    app.dependency_overrides[get_db_session] = database
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, environment="test", writing_approved_profile_fingerprints=[]
    )
    try:
        with TestClient(app) as client:
            yield client, authenticate, payload, session, writing_registry
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def approval_payload(metadata):
    return {
        key: metadata[key]
        for key in (
            "model_profile_id",
            "prompt_version_id",
            "agent_version_id",
            "workflow_version_id",
        )
    } | {"expected_profile_version": metadata["profile_version"]}


def assert_safe_metadata(metadata):
    assert set(metadata) == METADATA_KEYS
    assert PRIVATE_PROMPT not in str(metadata)
    assert "Private synthetic rubric-bound" not in str(metadata)
    assert "technical-fixture-only" not in str(metadata)
    assert metadata["external_processing_authorized"] is False
    assert metadata["availability"] == "UNCONFIGURED"


def test_create_then_independent_approval_exposes_only_safe_registry_metadata(profile_api):
    client, authenticate, payload, session, registry = profile_api
    authenticate()
    response = client.post(PREFIX, json=payload)
    assert response.status_code == 201, response.text
    metadata = response.json()
    assert_safe_metadata(metadata)
    assert metadata["registry_approved"] is False
    assert metadata["test_only"] is False
    for model, key in (
        (AIModelProfile, "model_profile_id"),
        (PromptVersion, "prompt_version_id"),
        (AgentVersion, "agent_version_id"),
        (AIWorkflowVersion, "workflow_version_id"),
    ):
        assert session.get(model, uuid.UUID(metadata[key])).status == AIArtifactStatus.DRAFT

    authenticate(1)
    approved = client.post(PREFIX + "/approve", json=approval_payload(metadata))
    assert approved.status_code == 200, approved.text
    assert_safe_metadata(approved.json())
    assert approved.json()["registry_approved"] is True
    assert approved.json()["profile_version"] == metadata["profile_version"]
    for model, key in (
        (AIModelProfile, "model_profile_id"),
        (PromptVersion, "prompt_version_id"),
        (AgentVersion, "agent_version_id"),
        (AIWorkflowVersion, "workflow_version_id"),
    ):
        expected = (
            AIArtifactStatus.ACTIVE if model is AIModelProfile else AIArtifactStatus.PUBLISHED
        )
        assert session.get(model, uuid.UUID(metadata[key])).status == expected

    audit = session.scalar(select(AuditEvent).where(AuditEvent.event_type == APPROVAL_EVENT))
    assert audit.actor_id == registry[1][1].id
    assert audit.metadata_json["qualification"] == "INDEPENDENT_WRITING_PROFILE_REVIEW"
    assert audit.metadata_json["test_only"] is False
    assert PRIVATE_PROMPT not in str(audit.metadata_json)
    # Registry approval creates neither an execution nor a candidate review outcome.
    assert session.scalar(select(func.count()).select_from(AIRun)) == 0
    assert session.scalar(select(func.count()).select_from(HumanReviewRequest)) == 0
    authenticate()
    repeated = client.post(PREFIX, json=payload)
    assert repeated.status_code == 201
    assert repeated.json()["registry_approved"] is True
    assert repeated.json()["profile_version"] == metadata["profile_version"]


def test_self_and_stale_bundle_approval_fail_without_an_approval_record(profile_api):
    client, authenticate, payload, session, _ = profile_api
    authenticate()
    created = client.post(PREFIX, json=payload)
    assert created.status_code == 201, created.text
    body = approval_payload(created.json())
    own = client.post(PREFIX + "/approve", json=body)
    assert own.status_code == 403
    assert "independent" in own.json()["detail"]
    authenticate(1)
    stale = client.post(PREFIX + "/approve", json={**body, "expected_profile_version": "f" * 64})
    assert stale.status_code == 409
    assert stale.json()["detail"] == "WRITING_PROFILE_VERSION_CONFLICT"
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == APPROVAL_EVENT)
        )
        == 0
    )


def test_forged_platform_permissions_are_revalidated_against_actual_database_roles(profile_api):
    client, authenticate, payload, session, registry = profile_api
    snapshot, users, creator, _, _ = registry
    forged = replace(creator, user_id=users[2].id)
    authenticate(2, forged)
    create = client.post(PREFIX, json=payload)
    approve = client.post(PREFIX + "/approve", json=approval_payload(snapshot))
    assert create.status_code == approve.status_code == 403
    assert (
        session.scalar(select(AIModelProfile).where(AIModelProfile.code == payload["code"])) is None
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == APPROVAL_EVENT)
        )
        == 0
    )


def test_routes_require_authenticated_platform_context_before_registry_access(profile_api):
    client, authenticate, payload, _, registry = profile_api
    snapshot, _, creator, _, _ = registry
    for path, body in (
        (PREFIX, payload),
        (PREFIX + "/approve", approval_payload(snapshot)),
    ):
        assert client.post(path, json=body).status_code == 401
    authenticate(0, replace(creator, organisation_id=uuid.uuid4()))
    for path, body in (
        (PREFIX, payload),
        (PREFIX + "/approve", approval_payload(snapshot)),
    ):
        assert client.post(path, json=body).status_code == 403


def test_manual_test_only_profile_approval_cannot_claim_human_registry_approval(profile_api):
    client, authenticate, payload, session, _ = profile_api
    authenticate()
    technical = {
        **payload,
        "provider": "DETERMINISTIC_TEST",
        "model_name": "writing-fixture-v1",
        "configuration": {"schema_version": "1", "adapter_version": "1"},
    }
    created = client.post(PREFIX, json=technical)
    assert created.status_code == 201, created.text
    assert created.json()["test_only"] is True
    assert created.json()["registry_approved"] is False
    authenticate(1)
    denied = client.post(PREFIX + "/approve", json=approval_payload(created.json()))
    assert denied.status_code == 409
    assert denied.json()["detail"] == "TEST_PROFILE_AUTOMATED_QUALIFICATION_ONLY"
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == APPROVAL_EVENT)
        )
        == 0
    )


def test_extra_fields_and_embedded_credentials_are_rejected_without_registry_artifacts(profile_api):
    client, authenticate, payload, session, registry = profile_api
    authenticate()
    for body in (
        {**payload, "registry_approved": True},
        {**payload, "approved_by": str(registry[1][1].id)},
    ):
        assert client.post(PREFIX, json=body).status_code == 422
    secret = client.post(
        PREFIX,
        json={**payload, "configuration": {**payload["configuration"], "api_key": "do-not-store"}},
    )
    assert secret.status_code == 409
    assert secret.json()["detail"] == "WRITING_CONFIGURATION_CONTAINS_CREDENTIALS"
    assert "do-not-store" not in secret.text
    assert (
        session.scalar(select(AIModelProfile).where(AIModelProfile.code == payload["code"])) is None
    )
    approve = client.post(
        PREFIX + "/approve", json={**approval_payload(registry[0]), "registry_approved": True}
    )
    assert approve.status_code == 422


def test_api_fingerprint_is_the_exact_persisted_model_prompt_agent_workflow_bundle(profile_api):
    client, authenticate, payload, session, _ = profile_api
    authenticate()
    created = client.post(PREFIX, json=payload)
    assert created.status_code == 201, created.text
    metadata = created.json()
    snapshot = snapshot_execution_profile(
        session,
        **{
            key: uuid.UUID(metadata[key])
            for key in (
                "model_profile_id",
                "prompt_version_id",
                "agent_version_id",
                "workflow_version_id",
            )
        },
    )
    assert metadata["execution_profile_id"] == metadata["workflow_version_id"]
    for key in (
        "profile_version",
        "model_version",
        "prompt_checksum",
        "agent_checksum",
        "workflow_checksum",
    ):
        assert metadata[key] == snapshot[key]
