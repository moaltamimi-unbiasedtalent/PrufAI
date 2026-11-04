"""Owned development prerequisites; no fabricated AI execution or outcome."""

import uuid

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models import (
    AIRun,
    AssessmentResult,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponseRevision,
    HumanReviewRequest,
    QuestionBankItemVersion,
    RubricVersion,
    ScoringRun,
    SpeechTranscriptionVersion,
    StandardPackVersion,
    SubjectiveAssessment,
    User,
)
from app.models.enums import RubricVersionStatus
from app.scripts.seed_ai_speaking_demo import (
    AI_SPEAKING_CANDIDATE_EMAIL,
    reconcile_ai_speaking_demo,
)
from app.scripts.seed_demo import reconcile_demo
from app.scripts.seed_speaking_human_demo import SPEAKING_HUMAN_POLICY_CONTENT, speaking_human_id


def test_ai_speaking_seed_is_exact_published_prerequisites_without_fake_execution(session):
    ids = reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    candidate = session.get(User, uuid.UUID(ids["ai_speaking_candidate_user_id"]))
    assert candidate.email == AI_SPEAKING_CANDIDATE_EMAIL
    pack = session.get(StandardPackVersion, uuid.UUID(ids["ai_speaking_pack_version_id"]))
    assert pack.standard_pack.code == "PRUFAI_AI_SPEAKING_B1"
    assert pack.standard_pack.language == "de" and pack.supported_levels == ["B1"]
    rubric = session.get(RubricVersion, uuid.UUID(ids["ai_speaking_rubric_version_id"]))
    assert rubric.status is RubricVersionStatus.PUBLISHED
    assert rubric.approved_by_user_id is not None and rubric.published_at is not None
    assert rubric.ai_assessment_policy == {
        "enabled": True,
        "mode": "AI_ASSISTED",
        "evidence_mode": "AUDIO",
        "execution_profile_id": ids["ai_speaking_execution_profile_id"],
        "execution_profile_version": ids["ai_speaking_execution_profile_version"],
    }
    item = session.get(QuestionBankItemVersion, uuid.UUID(ids["ai_speaking_item_version_id"]))
    assert item.rubric_reference == str(rubric.id)
    assert item.response_mode.value == "AUDIO"
    assert all(criterion["evidence_mode"] == "AUDIO" for criterion in rubric.criteria)
    assert rubric.evidence_policy["mode"] == "AUDIO"
    assert rubric.evidence_policy["source_backed"] is False
    assert all(binding.required is False for binding in rubric.evidence_bindings)
    assert all(binding.required is True for binding in pack.source_bindings)
    administrator = session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
    assert rubric.approved_by_user_id != administrator.id
    assert "TEST ONLY" in item.content["prompt"]
    assert "Es wird keine KI-Bewertung ausgeführt" not in item.content["prompt"]
    assert item.content["prompt"] != SPEAKING_HUMAN_POLICY_CONTENT
    assert str(rubric.standard_pack_version_id) == ids["ai_speaking_pack_version_id"]
    assert ids["speaking_human_pack_version_id"] != ids["ai_speaking_pack_version_id"]
    human = session.get(RubricVersion, uuid.UUID(ids["speaking_human_rubric_version_id"]))
    assert human.status is RubricVersionStatus.DRAFT
    assert human.ai_assessment_policy == {"enabled": False, "mode": "HUMAN_ONLY"}
    for model in (
        CandidateExamAssignment,
        CandidateExamAttempt,
        AssessmentResult,
        CandidateMediaAsset,
        ScoringRun,
        SubjectiveAssessment,
    ):
        assert (
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.candidate_user_id == candidate.id)
            )
            == 0
        )
    assert session.scalar(select(func.count()).select_from(AIRun)) == 0
    assert session.scalar(select(func.count()).select_from(CandidateMediaAsset)) == 0
    assert session.scalar(select(func.count()).select_from(SpeechTranscriptionVersion)) == 0
    assert (
        session.scalar(
            select(func.count())
            .select_from(CandidateResponseRevision)
            .join(
                CandidateExamAttempt,
                CandidateExamAttempt.id == CandidateResponseRevision.attempt_id,
            )
            .where(CandidateExamAttempt.candidate_user_id == candidate.id)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(HumanReviewRequest)
            .where(HumanReviewRequest.entity_type == "SUBJECTIVE_ASSESSMENT_VERSION")
        )
        == 0
    )


def test_ai_speaking_seed_is_idempotent_and_preserves_human_history(session):
    settings = Settings(_env_file=None, environment="development")
    first = reconcile_demo(session, settings, enabled=True)
    from app.models import (
        AgentVersion,
        AIModelProfile,
        AIWorkflowVersion,
        PromptVersion,
    )

    models = (
        User,
        AIModelProfile,
        PromptVersion,
        AgentVersion,
        AIWorkflowVersion,
        RubricVersion,
        HumanReviewRequest,
    )
    before = {model: session.scalar(select(func.count()).select_from(model)) for model in models}
    from app.models import AuditEvent

    approvals = session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.event_type == "speaking_execution.profile_approved")
    )
    second = reconcile_demo(session, settings, enabled=True)
    assert first == second
    assert before == {
        model: session.scalar(select(func.count()).select_from(model)) for model in models
    }
    assert approvals == session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.event_type == "speaking_execution.profile_approved")
    )
    assert (
        session.get(User, speaking_human_id("candidate")).email
        == "speaking.candidate@demo.prufai.local"
    )


def test_ai_speaking_seed_rejects_production_before_creating_data(session):
    with pytest.raises(RuntimeError, match="development"):
        reconcile_ai_speaking_demo(
            session,
            Settings(_env_file=None, environment="production", auth_rate_limit_backend="redis"),
            enabled=True,
            organisation=None,
            administrator=None,
            platform_operator=None,
            password="unused",
        )
    assert session.scalar(select(func.count()).select_from(User)) == 0


def test_ai_speaking_seed_cannot_claim_an_unrelated_candidate_email(session):
    user = User(email=AI_SPEAKING_CANDIDATE_EMAIL, normalized_email=AI_SPEAKING_CANDIDATE_EMAIL)
    session.add(user)
    session.commit()
    with pytest.raises(RuntimeError, match="identity collision"):
        reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
