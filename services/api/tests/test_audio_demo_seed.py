"""Original unsubmitted AUDIO prerequisites; SQLite is unit evidence only."""

import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models import (
    AIRun,
    CandidateAudioRecordingSession,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateMediaAsset,
    Exam,
    ExamVersion,
    HumanReviewRequest,
    KnowledgeDocumentPage,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    Organisation,
    OrganisationMembership,
    QuestionBankItemVersion,
    StandardPackVersion,
    User,
)
from app.models.enums import ExamVersionStatus, OrganisationType, StandardPackStatus
from app.runtime.service import CandidateRuntimeService
from app.scripts.seed_audio_demo import (
    AUDIO_CANDIDATE_EMAIL,
    AUDIO_EXAM_NAME,
    AUDIO_LABEL,
    AUDIO_POLICY,
    AUDIO_POLICY_CONTENT,
    audio_id,
)
from app.scripts.seed_demo import reconcile_demo


def assert_audio_seed_preconditions(session) -> dict[str, str]:
    before_media = [
        session.scalar(select(func.count()).select_from(model))
        for model in (CandidateMediaAsset, CandidateAudioRecordingSession, AIRun)
    ]
    ids = reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    candidate = session.get(User, uuid.UUID(ids["audio_candidate_user_id"]))
    assert candidate.email == AUDIO_CANDIDATE_EMAIL
    for model in (CandidateExamAssignment, CandidateExamAttempt):
        assert (
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.candidate_user_id == candidate.id)
            )
            == 0
        )
    assert [
        session.scalar(select(func.count()).select_from(model))
        for model in (CandidateMediaAsset, CandidateAudioRecordingSession, AIRun)
    ] == before_media
    pack = session.get(StandardPackVersion, uuid.UUID(ids["audio_pack_version_id"]))
    assert pack.status is StandardPackStatus.ACTIVE
    assert pack.standard_pack.language == "de"
    assert pack.supported_levels == ["B1"] and pack.allowed_classifications == ["PRACTICE"]
    exam = session.get(ExamVersion, uuid.UUID(ids["audio_exam_version_id"]))
    assert exam.status is ExamVersionStatus.ACTIVE and exam.exam.name == AUDIO_EXAM_NAME
    assert pack.approved_at and pack.published_at and exam.published_at
    review = session.scalar(
        select(HumanReviewRequest).where(
            HumanReviewRequest.entity_id == exam.id,
            HumanReviewRequest.entity_type == "EXAM_VERSION",
        )
    )
    assert review.status.value == "APPROVED" and review.reviewed_checksum == exam.checksum
    plan = CandidateRuntimeService._safe_plan(session, exam)
    delivered = [
        item
        for module in plan["modules"]
        for section in module["sections"]
        for task in section["tasks"]
        for item in task["items"]
    ]
    assert len(delivered) == 2
    assert {item["item_version_id"] for item in delivered} == {
        ids["audio_item_1_version_id"],
        ids["audio_item_2_version_id"],
    }
    for item in delivered:
        assert item["response_mode"] == "AUDIO"
        assert AUDIO_LABEL in item["content"]["prompt"]
        assert item["content"]["audio_policy"] == AUDIO_POLICY
        version = session.get(QuestionBankItemVersion, uuid.UUID(item["item_version_id"]))
        assert version.approved_at and version.published_at
        assert version.proposed_by_ai_run_id is None
    document = session.get(KnowledgeDocumentVersion, audio_id("policy-document"))
    assert document.notes == AUDIO_POLICY_CONTENT
    assert document.sha256 == hashlib.sha256(AUDIO_POLICY_CONTENT.encode()).hexdigest()
    assert document.storage_key is document.file_size is document.original_filename is None
    for model in (KnowledgeDocumentPage, KnowledgeIngestionJob):
        assert (
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.document_version_id == document.id)
            )
            == 0
        )
    return ids


def test_audio_seed_has_two_governed_original_tasks_and_no_evidence(session):
    assert_audio_seed_preconditions(session)


def test_audio_reseed_preserves_published_content_and_candidate_unassigned(session):
    ids = assert_audio_seed_preconditions(session)
    exam = session.get(ExamVersion, uuid.UUID(ids["audio_exam_version_id"]))
    items = [
        session.get(QuestionBankItemVersion, uuid.UUID(ids[f"audio_item_{i}_version_id"]))
        for i in (1, 2)
    ]
    before = (exam.checksum, exam.published_at, [(i.checksum, i.published_at) for i in items])
    assert assert_audio_seed_preconditions(session) == ids
    session.expire_all()
    assert (
        exam.checksum,
        exam.published_at,
        [(i.checksum, i.published_at) for i in items],
    ) == before


def test_audio_seed_cannot_claim_an_existing_unrelated_identity(session):
    user = User(email=AUDIO_CANDIDATE_EMAIL, normalized_email=AUDIO_CANDIDATE_EMAIL)
    session.add(user)
    session.commit()
    with pytest.raises(RuntimeError, match="Audio demo identity collision"):
        reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    session.rollback()
    assert session.get(User, user.id).password_credential is None
    assert session.scalar(select(func.count()).select_from(OrganisationMembership)) == 0


def test_audio_seed_refuses_foreign_tenant_membership_without_password_change(session):
    user = User(
        id=audio_id("candidate"),
        email=AUDIO_CANDIDATE_EMAIL,
        normalized_email=AUDIO_CANDIDATE_EMAIL,
    )
    org = Organisation(
        slug="audio-unrelated", name="Unrelated", organisation_type=OrganisationType.LANGUAGE_SCHOOL
    )
    session.add_all([user, org])
    session.flush()
    session.add(OrganisationMembership(user_id=user.id, organisation_id=org.id))
    session.commit()
    with pytest.raises(RuntimeError, match="Audio demo identity belongs to another tenant"):
        reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    session.rollback()
    assert session.get(User, user.id).password_credential is None
    assert session.scalar(select(func.count()).select_from(OrganisationMembership)) == 1


def test_audio_seed_refuses_foreign_exam_namespace_without_adopting_it(session):
    org = Organisation(
        slug="audio-exam-foreign",
        name="Foreign owner",
        organisation_type=OrganisationType.LANGUAGE_SCHOOL,
    )
    session.add(org)
    session.flush()
    exam = Exam(
        id=audio_id("exam"),
        code="FOREIGN_OWNED",
        name="Do not adopt",
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=org.id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(exam)
    session.commit()
    with pytest.raises(RuntimeError, match="Audio exam scope collision"):
        reconcile_demo(session, Settings(_env_file=None, environment="development"), enabled=True)
    session.rollback()
    assert session.get(Exam, exam.id).organisation_id == org.id
    assert session.get(Exam, exam.id).name == "Do not adopt"
