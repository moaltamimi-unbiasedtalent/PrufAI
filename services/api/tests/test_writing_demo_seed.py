"""Unit/service evidence for an unsubmitted HUMAN Writing development fixture."""

import hashlib
import uuid

import pytest
from sqlalchemy import func, select

from app.authorization import build_tenant_context
from app.config import Settings
from app.models import (
    AIRun,
    AssessmentResult,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateResponse,
    ExamVersion,
    HumanReviewRequest,
    KnowledgeDocumentPage,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    Organisation,
    OrganisationMembership,
    QuestionBankItemVersion,
    RubricVersion,
    ScoringRun,
    StandardPackVersion,
    SubjectiveAssessment,
    User,
)
from app.models.enums import (
    AssessmentClassification,
    AssessmentFamily,
    ExamVersionStatus,
    OrganisationType,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankScoringPolicy,
    QuestionBankSkill,
    RubricVersionStatus,
    StandardPackStatus,
)
from app.runtime.service import CandidateRuntimeService
from app.scripts.seed_demo import reconcile_demo
from app.scripts.seed_writing_demo import (
    WRITING_CANDIDATE_EMAIL,
    WRITING_EXAM_NAME,
    WRITING_LABEL,
    WRITING_POLICY_CONTENT,
    writing_id,
)


def assert_writing_seed_preconditions(session) -> dict[str, str]:
    previous_ai_runs = session.scalar(select(func.count()).select_from(AIRun))
    identifiers = reconcile_demo(
        session, Settings(_env_file=None, environment="development"), enabled=True
    )
    candidate = session.get(User, uuid.UUID(identifiers["writing_candidate_user_id"]))
    assert candidate is not None and candidate.email == WRITING_CANDIDATE_EMAIL
    context = build_tenant_context(session, candidate.id, uuid.UUID(identifiers["organisation_id"]))
    assert context.permissions == frozenset({"organisation.read"})
    assert context.platform_permissions == frozenset()
    for model in (CandidateExamAssignment, CandidateExamAttempt, AssessmentResult, ScoringRun):
        assert (
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.candidate_user_id == candidate.id)
            )
            == 0
        )
    assert (
        session.scalar(
            select(func.count())
            .select_from(SubjectiveAssessment)
            .where(SubjectiveAssessment.candidate_user_id == candidate.id)
        )
        == 0
    )
    assert session.scalar(select(func.count()).select_from(AIRun)) == previous_ai_runs
    pack = session.get(StandardPackVersion, uuid.UUID(identifiers["writing_pack_version_id"]))
    assert pack is not None and pack.status is StandardPackStatus.ACTIVE
    assert pack.standard_pack.assessment_family is AssessmentFamily.LANGUAGE
    assert pack.standard_pack.language == "de"
    assert pack.supported_levels == ["B1"] and pack.allowed_classifications == ["PRACTICE"]
    assert pack.approved_at is not None and pack.published_at is not None
    rubric = session.get(RubricVersion, uuid.UUID(identifiers["writing_rubric_version_id"]))
    assert rubric is not None and rubric.status is RubricVersionStatus.DRAFT
    assert str(rubric.standard_pack_version_id) == identifiers["writing_pack_version_id"]
    assert str(rubric.task_spec_id) == identifiers["writing_task_spec_id"]
    assert rubric.skill is QuestionBankSkill.WRITING and rubric.language == "de"
    assert rubric.cefr_levels == ["B1"]
    assert rubric.approved_at is None and rubric.published_at is None
    assert rubric.ai_assessment_policy == {"enabled": False, "mode": "HUMAN_ONLY"}
    assert {criterion["code"] for criterion in rubric.criteria} == {"TASK", "LANGUAGE"}
    assert sum(int(criterion["max_points"]) for criterion in rubric.criteria) == 6
    assert (
        session.scalar(
            select(func.count())
            .select_from(HumanReviewRequest)
            .where(
                HumanReviewRequest.entity_id == rubric.id,
            )
        )
        == 0
    )
    item = session.get(QuestionBankItemVersion, uuid.UUID(identifiers["writing_item_version_id"]))
    assert item is not None and item.status is QuestionBankLifecycleStatus.ACTIVE
    assert item.skill is QuestionBankSkill.WRITING
    assert item.response_mode is PackResponseMode.LONG_TEXT
    assert item.scoring_policy is QuestionBankScoringPolicy.RUBRIC_REQUIRED
    assert item.rubric_reference == str(rubric.id)
    assert item.approved_at is not None and item.published_at is not None
    assert item.proposed_by_ai_run_id is None
    exam = session.get(ExamVersion, uuid.UUID(identifiers["writing_exam_version_id"]))
    assert exam is not None and exam.exam.name == WRITING_EXAM_NAME
    assert exam.status is ExamVersionStatus.ACTIVE
    assert exam.classification is AssessmentClassification.PRACTICE
    assert exam.published_at is not None
    plan = CandidateRuntimeService._safe_plan(session, exam)
    delivered = plan["modules"][0]["sections"][0]["tasks"][0]["items"][0]
    assert delivered["response_mode"] == "LONG_TEXT"
    assert delivered["item_version_id"] == str(item.id)
    assert WRITING_LABEL in delivered["content"]["prompt"]
    assert "Hausaufgaben" in delivered["content"]["prompt"]
    assert "keine KI-Bewertung" in delivered["content"]["prompt"]
    document = session.get(KnowledgeDocumentVersion, writing_id("policy-document"))
    assert document is not None and document.notes == WRITING_POLICY_CONTENT
    assert document.sha256 == hashlib.sha256(WRITING_POLICY_CONTENT.encode()).hexdigest()
    assert document.storage_key is None and document.original_filename is None
    assert document.file_size is None
    for model in (KnowledgeIngestionJob, KnowledgeDocumentPage):
        assert (
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(
                    model.document_version_id == document.id,
                )
            )
            == 0
        )
    return identifiers


def test_writing_seed_has_draft_rubric_and_real_governed_unsubmitted_practice(session):
    assert_writing_seed_preconditions(session)


def test_writing_reseed_preserves_draft_and_published_history_without_fake_evidence(session):
    identifiers = assert_writing_seed_preconditions(session)
    models = (User, ExamVersion, QuestionBankItemVersion, RubricVersion, CandidateResponse, AIRun)
    session.expire_all()
    rubric = session.get(RubricVersion, uuid.UUID(identifiers["writing_rubric_version_id"]))
    exam = session.get(ExamVersion, uuid.UUID(identifiers["writing_exam_version_id"]))
    item = session.get(QuestionBankItemVersion, uuid.UUID(identifiers["writing_item_version_id"]))
    before = (
        rubric.checksum,
        rubric.status,
        exam.checksum,
        exam.published_at,
        item.checksum,
        item.published_at,
    )
    counts = [session.scalar(select(func.count()).select_from(model)) for model in models]
    assert assert_writing_seed_preconditions(session) == identifiers
    session.expire_all()
    assert (
        rubric.checksum,
        rubric.status,
        exam.checksum,
        exam.published_at,
        item.checksum,
        item.published_at,
    ) == before
    assert [session.scalar(select(func.count()).select_from(model)) for model in models] == counts


def test_writing_seed_cannot_claim_an_unrelated_user_email(session):
    unrelated = User(email=WRITING_CANDIDATE_EMAIL, normalized_email=WRITING_CANDIDATE_EMAIL)
    session.add(unrelated)
    session.commit()
    unrelated_id = unrelated.id
    with pytest.raises(RuntimeError, match="Writing demo identity collision"):
        reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    session.rollback()
    assert session.get(User, unrelated_id).password_credential is None
    assert session.scalar(select(func.count()).select_from(OrganisationMembership)) == 0


def test_writing_seed_cannot_reassign_an_existing_identity_from_another_tenant(session):
    user = User(
        id=writing_id("candidate"),
        email=WRITING_CANDIDATE_EMAIL,
        normalized_email=WRITING_CANDIDATE_EMAIL,
    )
    foreign = Organisation(
        slug="unrelated-writing-tenant",
        name="Unrelated tenant",
        organisation_type=OrganisationType.LANGUAGE_SCHOOL,
    )
    session.add_all([user, foreign])
    session.flush()
    session.add(OrganisationMembership(user_id=user.id, organisation_id=foreign.id))
    session.commit()
    with pytest.raises(RuntimeError, match="Writing demo identity has another tenant"):
        reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    session.rollback()
    assert session.get(User, user.id).password_credential is None
    assert session.scalar(select(func.count()).select_from(OrganisationMembership)) == 1
