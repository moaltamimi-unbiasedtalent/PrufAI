"""Unit/service checks for the explicit synthetic demo bootstrap."""

import uuid

import pytest
from sqlalchemy import delete, func, select

from app.auth.passwords import verify_password
from app.authorization import build_tenant_context
from app.config import Settings
from app.models import (
    AssessmentResult,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateMediaAsset,
    ExamVersion,
    MembershipRole,
    Organisation,
    OrganisationMembership,
    ResultVersion,
    User,
    UserPasswordCredential,
)
from app.models.enums import CandidateAttemptStatus
from app.runtime.service import CandidateRuntimeService
from app.scripts.seed_demo import PASSWORD, demo_id, reconcile_demo, require_demo_environment


def _settings() -> Settings:
    return Settings(_env_file=None, environment="development")


def test_demo_seed_requires_explicit_development_environment() -> None:
    with pytest.raises(RuntimeError, match="ENVIRONMENT=development"):
        require_demo_environment(_settings(), enabled=False)
    with pytest.raises(RuntimeError, match="ENVIRONMENT=development"):
        require_demo_environment(
            Settings(_env_file=None, environment="production", auth_rate_limit_backend="redis"),
            enabled=True,
        )


def test_demo_seed_reconciles_partial_records_and_keeps_unrelated_data(session) -> None:
    unrelated = User(
        email="developer@example.invalid", normalized_email="developer@example.invalid"
    )
    session.add(unrelated)
    session.commit()
    first = reconcile_demo(session, _settings(), enabled=True)
    models = (
        User,
        Organisation,
        ExamVersion,
        CandidateExamAssignment,
        CandidateExamAttempt,
        AssessmentResult,
        ResultVersion,
    )
    counts = {
        model.__name__: session.scalar(select(func.count()).select_from(model)) for model in models
    }
    second = reconcile_demo(session, _settings(), enabled=True)
    assert first == second
    assert counts == {
        model.__name__: session.scalar(select(func.count()).select_from(model)) for model in models
    }
    assert session.get(User, unrelated.id) is not None
    candidate = session.scalar(select(User).where(User.email == "candidate@demo.prufai.local"))
    membership = session.scalar(
        select(OrganisationMembership).where(
            OrganisationMembership.user_id == candidate.id,
            OrganisationMembership.organisation_id == uuid.UUID(first["organisation_id"]),
        )
    )
    session.execute(delete(MembershipRole).where(MembershipRole.membership_id == membership.id))
    session.execute(
        delete(UserPasswordCredential).where(UserPasswordCredential.user_id == candidate.id)
    )
    session.delete(
        session.get(CandidateExamAssignment, uuid.UUID(first["candidate_assignment_id"]))
    )
    session.commit()
    session.expire_all()
    restored = reconcile_demo(session, _settings(), enabled=True)
    assert restored["organisation_id"] == first["organisation_id"]
    assert session.get(User, unrelated.id) is not None
    session.expire_all()
    assert verify_password(candidate.password_credential.password_hash, PASSWORD)
    context = build_tenant_context(session, candidate.id, uuid.UUID(first["organisation_id"]))
    assert context.permissions == frozenset({"organisation.read"})
    assert session.get(CandidateExamAssignment, uuid.UUID(restored["candidate_assignment_id"]))
    assert session.scalar(select(func.count()).select_from(CandidateMediaAsset)) == 0


def assert_demo_runtime_roundtrip(session) -> None:
    identifiers = reconcile_demo(session, _settings(), enabled=True)
    candidate = session.scalar(select(User).where(User.email == "candidate@demo.prufai.local"))
    organisation_id = uuid.UUID(identifiers["organisation_id"])
    context = build_tenant_context(session, candidate.id, organisation_id)
    assignment_id = uuid.UUID(identifiers["candidate_assignment_id"])
    attempt = CandidateRuntimeService.create_attempt(session, context, assignment_id)
    CandidateRuntimeService.start(session, context, attempt.id)
    session.commit()
    session.expire_all()
    assert attempt.session is not None
    assert attempt.status is CandidateAttemptStatus.IN_PROGRESS
    state = CandidateRuntimeService.runtime_state(session, context, attempt.id)
    item = state["runtime_plan"]["modules"][0]["sections"][0]["tasks"][0]["items"][0]
    assert "9 Uhr" in item["content"]["prompt"]
    assert len(item["content"]["options"]) == 2
    assert "correct_option_ids" not in item["content"]
    binding_id = uuid.UUID(item["binding_id"])
    first = CandidateRuntimeService.save_response(
        session,
        context,
        attempt.id,
        binding_id,
        payload={"option_id": "b"},
        expected_revision=0,
        client_operation_id=uuid.uuid4(),
    )
    assert first.current_revision == 1
    second = CandidateRuntimeService.save_response(
        session,
        context,
        attempt.id,
        binding_id,
        payload={"option_id": "a"},
        expected_revision=1,
        client_operation_id=uuid.uuid4(),
    )
    assert second.current_revision == 2
    session.commit()
    session.expire_all()
    resumed = CandidateRuntimeService.runtime_state(session, context, attempt.id)
    assert resumed["responses"][0]["revision"] == 2
    assert resumed["responses"][0]["payload"] == {"option_id": "a"}
    CandidateRuntimeService.submit(session, context, attempt.id)
    session.commit()
    same = reconcile_demo(session, _settings(), enabled=True)
    assert same == identifiers
    assert session.get(CandidateExamAttempt, attempt.id).status is CandidateAttemptStatus.SUBMITTED
    assert session.get(ExamVersion, demo_id("exam-version")).modules
    historical = session.get(ResultVersion, uuid.UUID(identifiers["historical_result_version_id"]))
    assert historical.status.value == "RELEASED"
    assert historical.raw_awarded_points == 1
    assert historical.raw_maximum_points == 1


def test_demo_seed_has_a_real_runtime_and_historical_result(session) -> None:
    assert_demo_runtime_roundtrip(session)
