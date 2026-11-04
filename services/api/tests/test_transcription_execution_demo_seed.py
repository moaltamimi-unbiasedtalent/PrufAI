"""Explicit TEST ONLY prerequisites never manufacture executed evidence."""

import uuid

import pytest
from sqlalchemy import func, select

from app.authorization import build_tenant_context
from app.config import Settings
from app.models import (
    AIModelProfile,
    CandidateExamAssignment,
    CandidateMediaAsset,
    ExamVersion,
    QuestionBankItemVersion,
    SpeechTranscription,
    SpeechTranscriptionVersion,
    SubjectiveAssessment,
    User,
)
from app.models.enums import AIArtifactStatus, ExamVersionStatus, PackResponseMode
from app.runtime.service import CandidateRuntimeService
from app.scripts.seed_demo import reconcile_demo
from app.scripts.seed_transcription_execution_demo import (
    TRANSCRIPTION_CANDIDATE_EMAIL,
    TRANSCRIPTION_EXAM_NAME,
    TRANSCRIPTION_PROFILE_CODE,
    TRANSCRIPTION_PROFILE_CONFIGURATION,
    TRANSCRIPTION_PROFILE_NAME,
)


def test_execution_seed_is_distinct_unassigned_and_contains_no_transcription_evidence(session):
    settings = Settings(_env_file=None, environment="development")
    ids = reconcile_demo(session, settings, enabled=True)
    candidate = session.get(User, uuid.UUID(ids["transcription_execution_candidate_user_id"]))
    assert candidate.email == TRANSCRIPTION_CANDIDATE_EMAIL
    context = build_tenant_context(session, candidate.id, uuid.UUID(ids["organisation_id"]))
    assert context.permissions == frozenset({"organisation.read"})
    assert not context.platform_permissions
    for model in (CandidateExamAssignment, CandidateMediaAsset, SubjectiveAssessment):
        assert (
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.candidate_user_id == candidate.id)
            )
            == 0
        )
    assert session.scalar(select(func.count()).select_from(SpeechTranscription)) == 0
    assert session.scalar(select(func.count()).select_from(SpeechTranscriptionVersion)) == 0
    profile = session.get(AIModelProfile, uuid.UUID(ids["transcription_execution_profile_id"]))
    assert profile.name == TRANSCRIPTION_PROFILE_NAME
    assert profile.provider == "DETERMINISTIC_TEST"
    assert profile.model_name == "fixture-transcriber"
    assert profile.configuration == TRANSCRIPTION_PROFILE_CONFIGURATION
    assert profile.status is AIArtifactStatus.ACTIVE
    assert profile.approved_by == uuid.UUID(ids["platform_admin_user_id"])
    assert profile.approved_at is not None
    exam = session.get(ExamVersion, uuid.UUID(ids["transcription_execution_exam_version_id"]))
    assert exam.exam.name == TRANSCRIPTION_EXAM_NAME
    assert exam.status is ExamVersionStatus.ACTIVE
    item = session.get(
        QuestionBankItemVersion, uuid.UUID(ids["transcription_execution_item_version_id"])
    )
    assert item.response_mode is PackResponseMode.AUDIO and item.rubric_reference is None
    assert item.id != uuid.UUID(ids["speaking_human_item_version_id"])
    assert item.id != uuid.UUID(ids["speaking_results_item_version_id"])
    plan = CandidateRuntimeService._safe_plan(session, exam)
    delivered = [
        item
        for module in plan["modules"]
        for section in module["sections"]
        for task in section["tasks"]
        for item in task["items"]
    ]
    assert len(delivered) == 1
    assert "TEST ONLY" in delivered[0]["content"]["prompt"]
    counts = {
        model: session.scalar(select(func.count()).select_from(model))
        for model in (User, AIModelProfile, CandidateMediaAsset, SpeechTranscriptionVersion)
    }
    assert reconcile_demo(session, settings, enabled=True) == ids
    assert counts == {
        model: session.scalar(select(func.count()).select_from(model)) for model in counts
    }


def test_execution_seed_refuses_unrelated_candidate_identity(session):
    user = User(email=TRANSCRIPTION_CANDIDATE_EMAIL, normalized_email=TRANSCRIPTION_CANDIDATE_EMAIL)
    session.add(user)
    session.commit()
    with pytest.raises(RuntimeError, match="identity collision"):
        reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    session.rollback()
    assert session.get(User, user.id).password_credential is None


def test_execution_seed_refuses_unrelated_profile_and_never_approves_it(session):
    profile = AIModelProfile(
        code=TRANSCRIPTION_PROFILE_CODE,
        name="Unrelated existing profile",
        provider="OTHER",
        model_name="other",
        purpose="SPEECH_TRANSCRIPTION",
        status=AIArtifactStatus.DRAFT,
        configuration={},
    )
    session.add(profile)
    session.commit()
    with pytest.raises(RuntimeError, match="profile namespace collision"):
        reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    session.rollback()
    assert session.get(AIModelProfile, profile.id).status is AIArtifactStatus.DRAFT
    assert session.get(AIModelProfile, profile.id).approved_by is None


def test_execution_seed_refuses_production_before_creating_users_or_profiles(session):
    with pytest.raises(RuntimeError, match="requires ENVIRONMENT=development"):
        reconcile_demo(
            session,
            Settings(_env_file=None, environment="development").model_copy(
                update={"environment": "production"}
            ),
            enabled=True,
        )
    assert session.scalar(select(func.count()).select_from(User)) == 0
    assert session.scalar(select(func.count()).select_from(AIModelProfile)) == 0
