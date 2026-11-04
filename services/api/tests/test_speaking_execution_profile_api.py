"""Registry/RBAC API unit evidence, not real PostgreSQL or provider execution.

The Azure account/deployment metadata check is replaced only in this unit test;
no network request, candidate audio, inference, proposal or result is created.
"""

import copy
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth.api import Principal, get_principal
from app.authorization import build_tenant_context
from app.authorization.seed import seed_defaults
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
    PlatformRoleAssignment,
    PromptVersion,
    Role,
    SpeakingExecutionJob,
    SubjectiveAssessmentVersion,
    User,
)
from app.models.enums import AIArtifactStatus, PlatformAssignmentStatus, UserStatus
from app.speaking_execution import registry, runtime
from app.writing_execution.registry import snapshot_execution_profile as writing_snapshot
from tests.test_writing_execution_registry import writing_registry  # noqa: F401

PREFIX = "/api/v1/platform/ai/speaking-execution-profiles"
PRIVATE_PROMPT = "PRIVATE synthetic audio assessment instructions; never platform metadata."
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
    "model_revision",
    "native_structured_outputs",
    "output_contract",
}


def azure_configuration():
    return {
        "schema_version": "1",
        "adapter_version": "1",
        "model_revision": runtime.MODEL_REVISION,
        "output_contract": runtime.OUTPUT_CONTRACT,
        "endpoint": "https://speaking-unit.openai.azure.com",
        "resource_name": "speaking-unit",
        "subscription_id": str(uuid.uuid4()),
        "resource_group": "speaking-unit-resources",
        "deployment_id": "speaking-unit-deployment",
        "resource_region": "swedencentral",
        "processing_scope": "GLOBAL",
        "deployment_type": "GlobalStandard",
        "version_upgrade_policy": "NoAutoUpgrade",
        "expected_account_etag": '"unit-account-etag"',
        "expected_deployment_etag": '"unit-deployment-etag"',
        "credential_reference": runtime.CREDENTIAL_REFERENCE,
        "management_credential_reference": runtime.MANAGEMENT_CREDENTIAL_REFERENCE,
        "model_configuration_version": "unit-config-v1",
        "processing_approval_reference": "unit-only-processing-reference",
        "processing_approval_valid_until": (
            datetime.now(timezone.utc) + timedelta(days=1)
        ).isoformat(),
        "processing": {key: "unit-only-reference" for key in runtime.PROCESSING_REFERENCES},
    }


@pytest.fixture
def speaking_profile_api(session, monkeypatch):
    previous = dict(app.dependency_overrides)
    seed_defaults(session)
    role = session.scalar(select(Role).where(Role.key == "PRUFAI_SUPER_ADMIN"))
    users = [
        User(
            email=f"speaking-profile-{index}@test.local",
            normalized_email=f"speaking-profile-{index}@test.local",
            status=UserStatus.ACTIVE,
        )
        for index in range(3)
    ]
    session.add_all(users)
    session.flush()
    for user in users[:2]:
        session.add(
            PlatformRoleAssignment(
                user_id=user.id, role_id=role.id, status=PlatformAssignmentStatus.ACTIVE
            )
        )
    session.commit()
    creator, reviewer = (build_tenant_context(session, user.id) for user in users[:2])
    settings = Settings(
        _env_file=None, environment="test", speaking_approved_profile_fingerprints=[]
    )
    # Explicit unit placeholders: there are no real Azure credentials or network calls.
    monkeypatch.setenv(runtime.CREDENTIAL_REFERENCE, "unit-api-credential-placeholder")
    monkeypatch.setenv(runtime.MANAGEMENT_CREDENTIAL_REFERENCE, "unit-management-placeholder")
    verification = Mock(
        side_effect=lambda snapshot, _settings: runtime.deployment_fingerprint(
            snapshot["configuration"]
        )
    )
    monkeypatch.setattr(runtime, "verify_deployment", verification)

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

    payload = {
        "code": "speaking-api-contract",
        "name": "Synthetic Speaking API contract",
        "provider": runtime.PROVIDER,
        "model_name": runtime.MODEL,
        "configuration": azure_configuration(),
        "system_template": PRIVATE_PROMPT,
        "instruction_template": "Private rubric-bound original-audio instructions.",
    }
    app.dependency_overrides[get_db_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            yield (
                client,
                authenticate,
                payload,
                session,
                users,
                creator,
                reviewer,
                settings,
                verification,
            )
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
    for private in (
        PRIVATE_PROMPT,
        "Private rubric-bound",
        "unit-only-reference",
        "unit-api-credential-placeholder",
        "unit-management-placeholder",
        runtime.CREDENTIAL_REFERENCE,
        runtime.MANAGEMENT_CREDENTIAL_REFERENCE,
        "speaking-unit.openai.azure.com",
    ):
        assert private not in str(metadata)
    assert metadata["external_processing_authorized"] is False
    assert metadata["native_structured_outputs"] is False
    assert metadata["output_contract"] == "LOCAL_VALIDATED_JSON"
    assert metadata["model_revision"] == "2026-02-23"


def test_exact_azure_bundle_draft_then_independent_approval_has_safe_metadata_without_execution(
    speaking_profile_api,
):
    client, authenticate, payload, session, users, _, _, settings, verification = (
        speaking_profile_api
    )
    authenticate()
    created = client.post(PREFIX, json=payload)
    assert created.status_code == 201, created.text
    metadata = created.json()
    assert_safe_metadata(metadata)
    assert metadata["registry_approved"] is False and metadata["test_only"] is False
    assert metadata["availability"] == "UNCONFIGURED"
    assert verification.call_count == 0
    for model, key in (
        (AIModelProfile, "model_profile_id"),
        (PromptVersion, "prompt_version_id"),
        (AgentVersion, "agent_version_id"),
        (AIWorkflowVersion, "workflow_version_id"),
    ):
        assert session.get(model, uuid.UUID(metadata[key])).status == AIArtifactStatus.DRAFT
    assert (
        session.get(
            AIModelProfile, uuid.UUID(metadata["model_profile_id"])
        ).supports_structured_output
        is False
    )
    settings.speaking_approved_profile_fingerprints = [metadata["profile_version"]]
    authenticate(1)
    approved = client.post(PREFIX + "/approve", json=approval_payload(metadata))
    assert approved.status_code == 200, approved.text
    assert_safe_metadata(approved.json())
    assert approved.json()["registry_approved"] is True
    assert approved.json()["availability"] == "CONFIGURED"
    assert approved.json()["profile_version"] == metadata["profile_version"]
    assert verification.call_count == 1
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
    event = session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == registry.APPROVAL_EVENT)
    )
    assert event.actor_id == users[1].id
    assert event.metadata_json["qualification"] == "INDEPENDENT_SPEAKING_PROFILE_REVIEW"
    assert event.metadata_json["test_only"] is False
    assert PRIVATE_PROMPT not in str(event.metadata_json)
    for model in (SpeakingExecutionJob, AIRun, HumanReviewRequest, SubjectiveAssessmentVersion):
        assert session.scalar(select(func.count()).select_from(model)) == 0
    authenticate()
    repeated = client.post(PREFIX, json=payload)
    assert repeated.status_code == 201
    assert repeated.json() == approved.json()


def test_self_stale_and_unconfigured_approvals_fail_without_approval_record(speaking_profile_api):
    client, authenticate, payload, session, _, _, _, _, verification = speaking_profile_api
    authenticate()
    created = client.post(PREFIX, json=payload)
    assert created.status_code == 201, created.text
    body = approval_payload(created.json())
    assert client.post(PREFIX + "/approve", json=body).status_code == 403
    authenticate(1)
    stale = client.post(PREFIX + "/approve", json={**body, "expected_profile_version": "f" * 64})
    assert stale.status_code == 409
    assert stale.json()["detail"] == "SPEAKING_PROFILE_VERSION_CONFLICT"
    unavailable = client.post(PREFIX + "/approve", json=body)
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"] == "SPEAKING_DEPLOYMENT_APPROVAL_UNCONFIGURED"
    assert verification.call_count == 0
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == registry.APPROVAL_EVENT)
        )
        == 0
    )


def test_forged_grants_and_revoked_current_role_cannot_create_or_approve(speaking_profile_api):
    client, authenticate, payload, session, users, creator, reviewer, settings, verification = (
        speaking_profile_api
    )
    authenticate()
    created = client.post(PREFIX, json=payload)
    assert created.status_code == 201, created.text
    settings.speaking_approved_profile_fingerprints = [created.json()["profile_version"]]
    authenticate(2, replace(reviewer, user_id=users[2].id))
    denied_create = client.post(PREFIX, json={**payload, "code": "forged-speaking"})
    denied_approve = client.post(PREFIX + "/approve", json=approval_payload(created.json()))
    assert denied_create.status_code == denied_approve.status_code == 403
    grant = session.scalar(
        select(PlatformRoleAssignment).where(PlatformRoleAssignment.user_id == users[1].id)
    )
    grant.status = PlatformAssignmentStatus.SUSPENDED
    session.commit()
    # The Principal still carries previously valid permissions; actual database grants win.
    authenticate(1, reviewer)
    denied_create = client.post(PREFIX, json={**payload, "code": "revoked-speaking"})
    denied_approve = client.post(PREFIX + "/approve", json=approval_payload(created.json()))
    assert denied_create.status_code == denied_approve.status_code == 403
    assert verification.call_count == 0
    assert (
        session.scalar(
            select(AIModelProfile).where(
                AIModelProfile.code.in_(["forged-speaking", "revoked-speaking"])
            )
        )
        is None
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == registry.APPROVAL_EVENT)
        )
        == 0
    )


def test_authentication_and_platform_scope_required_before_registry_access(speaking_profile_api):
    client, authenticate, payload, _, _, creator, _, _, _ = speaking_profile_api
    empty_approval = {
        key: str(uuid.uuid4())
        for key in (
            "model_profile_id",
            "prompt_version_id",
            "agent_version_id",
            "workflow_version_id",
        )
    } | {"expected_profile_version": "a" * 64}
    for path, body in ((PREFIX, payload), (PREFIX + "/approve", empty_approval)):
        assert client.post(path, json=body).status_code == 401
    authenticate(0, replace(creator, organisation_id=uuid.uuid4()))
    for path, body in ((PREFIX, payload), (PREFIX + "/approve", empty_approval)):
        assert client.post(path, json=body).status_code == 403


def test_test_only_profile_cannot_receive_manual_human_approval(speaking_profile_api):
    client, authenticate, payload, session, _, _, _, _, verification = speaking_profile_api
    authenticate()
    created = client.post(
        PREFIX,
        json={
            **payload,
            "provider": "DETERMINISTIC_TEST",
            "model_name": "speaking-fixture-v1",
            "configuration": {"schema_version": "1", "adapter_version": "1", "model_revision": "1"},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["test_only"] is True
    assert created.json()["registry_approved"] is False
    authenticate(1)
    denied = client.post(PREFIX + "/approve", json=approval_payload(created.json()))
    assert denied.status_code == 409
    assert denied.json()["detail"] == "TEST_PROFILE_AUTOMATED_QUALIFICATION_ONLY"
    assert verification.call_count == 0
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == registry.APPROVAL_EVENT)
        )
        == 0
    )


def test_extra_authority_fields_and_nested_credentials_cannot_create_artifacts(
    speaking_profile_api,
):
    client, authenticate, payload, session, users, _, _, _, verification = speaking_profile_api
    authenticate()
    for extra in (
        {"registry_approved": True},
        {"approved_by": str(users[1].id)},
        {"purpose": "WRITING_ASSESSMENT"},
        {"supports_structured_output": True},
    ):
        assert client.post(PREFIX, json={**payload, **extra}).status_code == 422
    for credential in (
        {"api_key": "do-not-store"},
        {"nested": {"bearer_token": "do-not-store"}},
        {"processing": {"authorization_header": "do-not-store"}},
    ):
        secret = client.post(
            PREFIX, json={**payload, "configuration": {**payload["configuration"], **credential}}
        )
        assert secret.status_code == 409
        assert secret.json()["detail"] == "SPEAKING_CONFIGURATION_CONTAINS_CREDENTIALS"
        assert "do-not-store" not in secret.text
    assert (
        session.scalar(select(AIModelProfile).where(AIModelProfile.code == payload["code"])) is None
    )
    assert verification.call_count == 0
    created = client.post(PREFIX, json=payload)
    assert created.status_code == 201, created.text
    for authority in (
        {"registry_approved": True},
        {"approved_by": str(users[1].id)},
        {"native_structured_outputs": True},
    ):
        denied = client.post(
            PREFIX + "/approve", json={**approval_payload(created.json()), **authority}
        )
        assert denied.status_code == 422
    assert verification.call_count == 0
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == registry.APPROVAL_EVENT)
        )
        == 0
    )


def test_exact_persisted_fingerprint_and_original_writing_bundle_stay_separate(
    speaking_profile_api,
    writing_registry,  # noqa: F811
):
    client, authenticate, payload, session, _, _, _, _, _ = speaking_profile_api
    writing = copy.deepcopy(writing_registry[0])
    authenticate()
    created = client.post(PREFIX, json=payload)
    assert created.status_code == 201, created.text
    metadata = created.json()
    ids = {
        key: uuid.UUID(metadata[key])
        for key in (
            "model_profile_id",
            "prompt_version_id",
            "agent_version_id",
            "workflow_version_id",
        )
    }
    snapshot = registry.snapshot_execution_profile(session, **ids)
    assert metadata["execution_profile_id"] == metadata["workflow_version_id"]
    for key in (
        "profile_version",
        "model_version",
        "prompt_checksum",
        "agent_checksum",
        "workflow_checksum",
    ):
        assert metadata[key] == snapshot[key]
    assert writing_snapshot(session, **{key: uuid.UUID(writing[key]) for key in ids}) == writing
    rejected = client.post(PREFIX + "/approve", json=approval_payload(writing))
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "SPEAKING_EXECUTION_PROFILE_INCOMPATIBLE"
    assert writing_snapshot(session, **{key: uuid.UUID(writing[key]) for key in ids}) == writing
    writing_payload = {
        key: value for key, value in writing_registry[4].items() if key != "context"
    } | {
        "code": "original-writing-api",
        "name": "Unchanged Writing API",
        "system_template": "Original Writing instructions.",
    }
    response = client.post("/api/v1/platform/ai/writing-execution-profiles", json=writing_payload)
    assert response.status_code == 201, response.text
    assert response.json()["provider"] == "OPENAI"
    assert "native_structured_outputs" not in response.json()
    assert writing_snapshot(session, **{key: uuid.UUID(writing[key]) for key in ids}) == writing
