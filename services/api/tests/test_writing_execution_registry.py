"""Registry/RBAC unit evidence; real PostgreSQL execution is tested separately."""

import uuid
from dataclasses import replace

import pytest
from sqlalchemy import func, select

from app.authorization import AuthorizationError, build_tenant_context
from app.authorization.seed import seed_defaults
from app.config import Settings
from app.models import (
    AgentVersion,
    AIModelProfile,
    AIWorkflowVersion,
    AuditEvent,
    PlatformRoleAssignment,
    PromptVersion,
    Role,
    User,
)
from app.models.enums import AIArtifactStatus, PlatformAssignmentStatus, UserStatus
from app.writing_execution.registry import (
    APPROVAL_EVENT,
    approve_execution_profile,
    approved_profile_record,
    create_execution_profile,
    qualify_test_execution_profile,
    snapshot_registry_valid,
)
from app.writing_execution.runtime import validate_execution_configuration
from tests.test_writing_provider_runtime import configured_snapshot


@pytest.fixture
def writing_registry(session):
    seed_defaults(session)
    role = session.scalar(select(Role).where(Role.key == "PRUFAI_SUPER_ADMIN"))
    users = [
        User(
            email=f"writing-profile-{i}@test.local",
            normalized_email=f"writing-profile-{i}@test.local",
            status=UserStatus.ACTIVE,
        )
        for i in range(3)
    ]
    session.add_all(users)
    session.flush()
    for user in users[:2]:
        session.add(
            PlatformRoleAssignment(
                user_id=user.id, role_id=role.id, status=PlatformAssignmentStatus.ACTIVE
            )
        )
    session.flush()
    creator, reviewer = (build_tenant_context(session, user.id) for user in users[:2])
    arguments = dict(
        context=creator,
        code="writing-profile-contract",
        name="Writing contract",
        provider="OPENAI",
        model_name=configured_snapshot()["model_name"],
        configuration=configured_snapshot()["configuration"],
        system_template="Original synthetic governed Writing instructions.",
    )
    snapshot = create_execution_profile(session, **arguments)
    session.commit()
    return snapshot, users, creator, reviewer, arguments


def approve(session, writing_registry):
    snapshot, _, _, reviewer, _ = writing_registry
    return approve_execution_profile(
        session,
        snapshot=snapshot,
        context=reviewer,
        settings=Settings(_env_file=None),
        expected_profile_version=snapshot["profile_version"],
    )


def test_exact_independent_bundle_approval_is_not_deployment_or_processing_authorization(
    session, writing_registry
):
    snapshot, _, _, _, args = writing_registry
    assert create_execution_profile(session, **args) == snapshot
    assert approved_profile_record(session, snapshot) is None
    approved = approve(session, writing_registry)
    assert approved["profile_version"] == snapshot["profile_version"]
    assert snapshot_registry_valid(session, approved)
    assert (
        validate_execution_configuration(approved, Settings(_env_file=None))["availability"]
        == "UNCONFIGURED"
    )
    assert approve(session, writing_registry) == approved
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == APPROVAL_EVENT)
        )
        == 1
    )
    record = approved_profile_record(session, snapshot)
    assert record.metadata_json["qualification"] == "INDEPENDENT_WRITING_PROFILE_REVIEW"
    assert "Original synthetic" not in str(record.metadata_json)


def test_self_approval_forged_grants_and_stale_fingerprint_cannot_approve(
    session, writing_registry
):
    snapshot, users, creator, reviewer, _ = writing_registry
    for context in (creator, replace(reviewer, user_id=users[2].id)):
        with pytest.raises(AuthorizationError):
            approve_execution_profile(
                session,
                snapshot=snapshot,
                context=context,
                settings=Settings(_env_file=None),
                expected_profile_version=snapshot["profile_version"],
            )
    with pytest.raises(ValueError, match="VERSION_CONFLICT"):
        approve_execution_profile(
            session,
            snapshot=snapshot,
            context=reviewer,
            settings=Settings(_env_file=None),
            expected_profile_version="f" * 64,
        )
    assert approved_profile_record(session, snapshot) is None


@pytest.mark.parametrize(
    "model,key,field,value",
    [
        (AIModelProfile, "model_profile_id", "model_name", "other-model"),
        (PromptVersion, "prompt_version_id", "system_template", "changed prompt"),
        (AgentVersion, "agent_version_id", "tool_policy", ["KNOWLEDGE_RETRIEVAL"]),
        (AIWorkflowVersion, "workflow_version_id", "graph_definition", {"nodes": []}),
    ],
)
def test_approved_registry_content_is_immutable(
    session, writing_registry, model, key, field, value
):
    approved = approve(session, writing_registry)
    session.commit()
    artifact = session.get(model, uuid.UUID(approved[key]))
    setattr(artifact, field, value)
    with pytest.raises(ValueError, match="immutable"):
        session.flush()
    session.rollback()
    assert snapshot_registry_valid(session, approved)


def test_approved_history_cannot_be_deleted_but_revocation_fails_closed(session, writing_registry):
    approved = approve(session, writing_registry)
    session.commit()
    artifact = session.get(AIModelProfile, uuid.UUID(approved["model_profile_id"]))
    session.delete(artifact)
    with pytest.raises(ValueError, match="cannot be deleted"):
        session.flush()
    session.rollback()
    artifact = session.get(AIModelProfile, uuid.UUID(approved["model_profile_id"]))
    artifact.status = AIArtifactStatus.DISABLED
    session.flush()
    assert snapshot_registry_valid(session, approved) is False
    assert approved_profile_record(session, approved) is None


def test_nested_in_place_configuration_mutation_is_rejected_even_without_a_flush(
    session, writing_registry
):
    approved = approve(session, writing_registry)
    session.commit()
    model = session.get(AIModelProfile, uuid.UUID(approved["model_profile_id"]))
    model.configuration["processing"]["legal_basis_reference"] = "unapproved-change"
    assert model not in session.dirty
    with pytest.raises(ValueError, match="immutable"):
        session.commit()
    session.rollback()
    assert snapshot_registry_valid(session, approved)


def test_profile_namespace_cannot_be_reused_with_changed_artifacts(session, writing_registry):
    _, _, _, _, args = writing_registry
    with pytest.raises(ValueError, match="NAMESPACE_CONFLICT"):
        create_execution_profile(session, **{**args, "system_template": "Replacement"})


def test_test_bundle_is_explicitly_technical_and_cannot_get_manual_human_approval(
    session, writing_registry
):
    _, users, _, reviewer, args = writing_registry
    technical = create_execution_profile(
        session,
        **{
            **args,
            "code": "writing-technical-only",
            "provider": "DETERMINISTIC_TEST",
            "model_name": "writing-fixture-v1",
            "configuration": {"schema_version": "1", "adapter_version": "1"},
        },
    )
    settings = Settings(_env_file=None, environment="test", writing_test_adapter_enabled=True)
    with pytest.raises(ValueError, match="AUTOMATED_QUALIFICATION_ONLY"):
        approve_execution_profile(
            session,
            snapshot=technical,
            context=reviewer,
            settings=settings,
            expected_profile_version=technical["profile_version"],
        )
    qualified = qualify_test_execution_profile(
        session, snapshot=technical, operator=users[0], settings=settings
    )
    assert snapshot_registry_valid(session, qualified)
    record = approved_profile_record(session, qualified)
    assert record.metadata_json["test_only"] is True
    assert record.metadata_json["qualification"] == "AUTOMATED_TECHNICAL_TEST"
    assert validate_execution_configuration(qualified, settings)["availability"] == "TEST_ONLY"


def test_invalid_configuration_never_qualifies_for_real_execution(session, writing_registry):
    _, _, _, reviewer, args = writing_registry
    invalid = create_execution_profile(
        session,
        **{
            **args,
            "code": "writing-wrong-region",
            "configuration": {**args["configuration"], "region": "US"},
        },
    )
    with pytest.raises(ValueError, match="CONFIGURATION_INVALID"):
        approve_execution_profile(
            session,
            snapshot=invalid,
            context=reviewer,
            settings=Settings(_env_file=None),
            expected_profile_version=invalid["profile_version"],
        )
    with pytest.raises(ValueError, match="CONTAINS_CREDENTIALS"):
        create_execution_profile(
            session,
            **{
                **args,
                "code": "writing-secret-rejected",
                "configuration": {"api_key": "never persist"},
            },
        )
