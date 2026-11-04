"""Registry approval security; real PostgreSQL proof is a separate required suite."""

from dataclasses import replace

import pytest
from sqlalchemy import func, select

from app.authorization import AuthorizationError, build_tenant_context
from app.authorization.seed import seed_defaults
from app.config import Settings
from app.models import AIModelProfile, AuditEvent, PlatformRoleAssignment, Role, User
from app.models.enums import AIArtifactStatus, PlatformAssignmentStatus, UserStatus
from app.speech_transcription.execution import snapshot_profile
from app.speech_transcription.profiles import approve_profile, profile_descriptor
from app.speech_transcription.runtime import validate_profile_configuration
from tests.test_transcription_runtime import fixture_snapshot, real_snapshot


@pytest.fixture
def profiles(session):
    seed_defaults(session)
    role = session.scalar(select(Role).where(Role.key == "PRUFAI_SUPER_ADMIN"))
    users = [
        User(
            email=f"profile-{i}@test.local",
            normalized_email=f"profile-{i}@test.local",
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
    profile = AIModelProfile(
        code="approved-transcription-unit",
        name="Transcription unit",
        provider="OPENAI",
        model_name="gpt-transcribe",
        purpose="SPEECH_TRANSCRIPTION",
        configuration=real_snapshot()["configuration"],
        status=AIArtifactStatus.DRAFT,
        created_by=users[0].id,
    )
    session.add(profile)
    session.flush()
    return profile, users, build_tenant_context(session, users[1].id)


def version(profile):
    return snapshot_profile(profile, "de", {})["profile_version"]


def test_independent_exact_profile_approval_does_not_approve_external_processing(session, profiles):
    profile, _, context = profiles
    runtime = Settings(_env_file=None)
    fingerprint = version(profile)
    assert (
        validate_profile_configuration(snapshot_profile(profile, "de", {}), runtime)["availability"]
        == "UNCONFIGURED"
    )
    approved = approve_profile(
        session,
        profile_id=profile.id,
        expected_version=fingerprint,
        context=context,
        settings=runtime,
    )
    assert approved.status == AIArtifactStatus.ACTIVE
    assert approved.approved_by == context.user_id and approved.approved_at is not None
    assert profile_descriptor(profile)["transcription_profile_version"] == fingerprint
    assert (
        validate_profile_configuration(snapshot_profile(profile, "de", {}), runtime)["availability"]
        == "UNCONFIGURED"
    )
    approve_profile(
        session,
        profile_id=profile.id,
        expected_version=fingerprint,
        context=context,
        settings=runtime,
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "speech_transcription.profile_approved")
        )
        == 1
    )


def test_self_approval_stale_fingerprint_and_forged_platform_permissions_are_rejected(
    session, profiles
):
    profile, users, reviewer = profiles
    runtime = Settings(_env_file=None)
    for context in (
        build_tenant_context(session, users[0].id),
        replace(reviewer, user_id=users[2].id),
    ):
        with pytest.raises(AuthorizationError):
            approve_profile(
                session,
                profile_id=profile.id,
                expected_version=version(profile),
                context=context,
                settings=runtime,
            )
    with pytest.raises(ValueError, match="VERSION_CONFLICT"):
        approve_profile(
            session,
            profile_id=profile.id,
            expected_version="f" * 64,
            context=reviewer,
            settings=runtime,
        )
    assert profile.status == AIArtifactStatus.DRAFT
    assert profile.approved_at is None


def test_active_profile_configuration_drift_cannot_replay_prior_approval(session, profiles):
    profile, _, context = profiles
    runtime = Settings(_env_file=None)
    prior = version(profile)
    approve_profile(
        session, profile_id=profile.id, expected_version=prior, context=context, settings=runtime
    )
    profile.configuration = {**profile.configuration, "model_configuration_version": "changed"}
    session.flush()
    for fingerprint in (prior, version(profile)):
        with pytest.raises(ValueError, match="VERSION_CONFLICT"):
            approve_profile(
                session,
                profile_id=profile.id,
                expected_version=fingerprint,
                context=context,
                settings=runtime,
            )


def test_invalid_provider_configuration_never_becomes_active(session, profiles):
    profile, _, context = profiles
    profile.configuration = {**profile.configuration, "endpoint": "http://unapproved.invalid"}
    session.flush()
    with pytest.raises(ValueError, match="CONFIGURATION_INVALID"):
        approve_profile(
            session,
            profile_id=profile.id,
            expected_version=version(profile),
            context=context,
            settings=Settings(_env_file=None),
        )
    assert profile.status == AIArtifactStatus.DRAFT and profile.approved_by is None


def test_manual_test_profile_approval_is_not_a_human_or_external_processing_approval(
    session, profiles
):
    profile, _, context = profiles
    profile.provider, profile.model_name = "DETERMINISTIC_TEST", "fixture-transcriber"
    profile.configuration = fixture_snapshot()["configuration"]
    session.flush()
    with pytest.raises(ValueError, match="AUTOMATED_APPROVAL_ONLY"):
        approve_profile(
            session,
            profile_id=profile.id,
            expected_version=version(profile),
            context=context,
            settings=Settings(
                _env_file=None, environment="test", speech_transcription_test_adapter_enabled=True
            ),
        )
    assert profile.status == AIArtifactStatus.DRAFT and profile.approved_by is None
