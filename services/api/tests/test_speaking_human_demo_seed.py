"""An explicitly unassessed, original HUMAN_ONLY/AUDIO development fixture."""

import uuid

import pytest
from sqlalchemy import func, select

from app.authorization import build_tenant_context
from app.config import Settings
from app.models import (
    AIRun,
    CandidateExamAssignment,
    CandidateMediaAsset,
    CandidateResponse,
    ExamVersion,
    QuestionBankItemVersion,
    QuestionBankStandardPackBinding,
    RubricVersion,
    StandardPackVersion,
    SubjectiveAssessment,
    User,
)
from app.models.enums import (
    AssessmentClassification,
    PackResponseMode,
    QuestionBankSkill,
    RubricVersionStatus,
)
from app.runtime.service import CandidateRuntimeService
from app.scripts.seed_demo import reconcile_demo
from app.scripts.seed_speaking_human_demo import (
    SPEAKING_HUMAN_CANDIDATE_EMAIL,
    SPEAKING_HUMAN_EXAM_NAME,
    SPEAKING_HUMAN_LABEL,
)


def assert_speaking_human_seed_preconditions(session):
    ids = reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    candidate = session.get(User, uuid.UUID(ids["speaking_human_candidate_user_id"]))
    assert candidate.email == SPEAKING_HUMAN_CANDIDATE_EMAIL
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
    version = session.get(RubricVersion, uuid.UUID(ids["speaking_human_rubric_version_id"]))
    assert version.status is RubricVersionStatus.DRAFT
    assert version.approved_at is version.published_at is None
    assert version.skill is QuestionBankSkill.SPEAKING
    assert version.ai_assessment_policy == {"enabled": False, "mode": "HUMAN_ONLY"}
    assert version.evidence_policy == {
        "mode": "AUDIO",
        "required": True,
        "minimum_references": 1,
        "time_unit": "milliseconds",
    }
    assert all(row["evidence_mode"] == "AUDIO" for row in version.criteria)
    assert str(version.task_spec_id) == ids["speaking_human_task_spec_id"]
    item = session.get(QuestionBankItemVersion, uuid.UUID(ids["speaking_human_item_version_id"]))
    assert item.rubric_reference == str(version.id)
    assert item.response_mode is PackResponseMode.AUDIO
    assert item.skill is QuestionBankSkill.SPEAKING
    assert (
        str(
            session.scalar(
                select(QuestionBankStandardPackBinding).where(
                    QuestionBankStandardPackBinding.item_version_id == item.id
                )
            ).standard_pack_version_id
        )
        == ids["speaking_human_pack_version_id"]
    )
    exam = session.get(ExamVersion, uuid.UUID(ids["speaking_human_exam_version_id"]))
    assert exam.exam.name == SPEAKING_HUMAN_EXAM_NAME
    assert exam.classification is AssessmentClassification.PRACTICE
    pack = session.get(StandardPackVersion, exam.standard_pack_version_id)
    assert pack.standard_pack.language == "de"
    assert pack.supported_levels == ["B1"]
    plan = CandidateRuntimeService._safe_plan(session, exam)
    delivered = plan["modules"][0]["sections"][0]["tasks"][0]["items"][0]
    assert delivered["item_version_id"] == str(item.id)
    assert SPEAKING_HUMAN_LABEL in delivered["content"]["prompt"]
    assert "Keine KI oder Transkription" in delivered["content"]["prompt"]
    return ids


def test_human_speaking_seed_remains_draft_unassigned_and_preserves_evidence_only_fixture(session):
    ids = assert_speaking_human_seed_preconditions(session)
    assert ids["speaking_human_item_version_id"] != ids["audio_item_1_version_id"]
    old = session.get(QuestionBankItemVersion, uuid.UUID(ids["audio_item_1_version_id"]))
    assert old.rubric_reference is None
    assert "Keine Bewertung" in old.content["prompt"]
    counts = [
        session.scalar(select(func.count()).select_from(model))
        for model in (User, RubricVersion, CandidateResponse, AIRun)
    ]
    assert assert_speaking_human_seed_preconditions(session) == ids
    assert [
        session.scalar(select(func.count()).select_from(model))
        for model in (User, RubricVersion, CandidateResponse, AIRun)
    ] == counts


def test_human_speaking_seed_cannot_claim_an_unrelated_user(session):
    user = User(
        email=SPEAKING_HUMAN_CANDIDATE_EMAIL, normalized_email=SPEAKING_HUMAN_CANDIDATE_EMAIL
    )
    session.add(user)
    session.commit()
    with pytest.raises(RuntimeError, match="identity collision"):
        reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    session.rollback()
    assert session.get(User, user.id).password_credential is None
