"""Unit/service regression for the unassigned integrated B1 development fixture."""

import hashlib
import uuid

import pytest
from sqlalchemy import func, select

from app.auth.service import AuthenticationService
from app.authorization import build_tenant_context
from app.config import Settings
from app.exams.service import ExamStudioService, exam_checksum
from app.models import (
    AssessmentResult,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateResponseRevision,
    HumanReviewRequest,
    KnowledgeDocumentPage,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    OrganisationMembership,
    QuestionBankItemVersion,
    ResultRelease,
    ResultVersion,
    ScoringRun,
    StandardPackVersion,
    StatementOfResults,
    User,
)
from app.models.enums import (
    AssessmentClassification,
    AssessmentFamily,
    CandidateAttemptStatus,
    ExamVersionStatus,
    HumanReviewStatus,
    QuestionBankLifecycleStatus,
    ResultVersionStatus,
    StandardPackStatus,
    UserStatus,
)
from app.runtime.service import CandidateRuntimeService
from app.scoring.service import ScoringService
from app.scripts.seed_demo import (
    GOLDEN_CANDIDATE_EMAIL,
    GOLDEN_CONTENT_LABEL,
    GOLDEN_POLICY_CONTENT,
    demo_id,
    reconcile_demo,
)
from app.standard_packs.service import StandardPackService


def _settings() -> Settings:
    return Settings(_env_file=None, environment="development")


def assert_golden_seed_preconditions(session) -> dict[str, str]:
    identifiers = reconcile_demo(session, _settings(), enabled=True)
    candidate = session.get(User, uuid.UUID(identifiers["golden_candidate_user_id"]))
    assert candidate is not None
    assert candidate.id == demo_id("golden-candidate")
    assert candidate.email == GOLDEN_CANDIDATE_EMAIL
    assert candidate.display_name == "Golden B1 Practice Candidate"
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
    pack = session.get(StandardPackVersion, uuid.UUID(identifiers["golden_pack_version_id"]))
    assert pack is not None
    assert pack.standard_pack.assessment_family is AssessmentFamily.LANGUAGE
    assert pack.standard_pack.language == "de"
    assert pack.supported_levels == ["B1"]
    assert pack.allowed_classifications == ["PRACTICE"]
    assert pack.status is StandardPackStatus.ACTIVE
    assert StandardPackService.validate(session, pack).status.value == "PASS"
    assert pack.configuration_checksum == StandardPackService.checksum(pack)
    assert pack.published_at is not None and pack.approved_at is not None
    assert str(pack.approved_by_user_id) == identifiers["platform_admin_user_id"]
    assert len(pack.source_bindings) == 1
    document = session.get(KnowledgeDocumentVersion, demo_id("golden-policy-document"))
    assert document is not None
    assert document.notes == GOLDEN_POLICY_CONTENT
    assert document.sha256 == hashlib.sha256(GOLDEN_POLICY_CONTENT.encode()).hexdigest()
    assert pack.source_bindings[0].document_version_id == document.id
    assert pack.source_bindings[0].document_checksum == document.sha256
    assert document.storage_key is None
    assert document.original_filename is None and document.file_size is None
    assert (
        session.scalar(
            select(func.count())
            .select_from(KnowledgeIngestionJob)
            .where(KnowledgeIngestionJob.document_version_id == document.id)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(KnowledgeDocumentPage)
            .where(KnowledgeDocumentPage.document_version_id == document.id)
        )
        == 0
    )
    from app.models import ExamVersion

    exam = session.get(ExamVersion, uuid.UUID(identifiers["golden_exam_version_id"]))
    assert exam is not None
    assert exam.id != demo_id("exam-version")
    assert exam.classification is AssessmentClassification.PRACTICE
    assert exam.status is ExamVersionStatus.ACTIVE
    assert exam.published_at is not None
    assert str(exam.published_by_user_id) == identifiers["platform_admin_user_id"]
    assert exam.checksum == exam_checksum(ExamStudioService._payload(session, exam))
    review = session.scalar(
        select(HumanReviewRequest).where(
            HumanReviewRequest.entity_id == exam.id,
            HumanReviewRequest.entity_type == "EXAM_VERSION",
        )
    )
    assert review is not None and review.status is HumanReviewStatus.APPROVED
    assert review.reviewed_checksum == exam.checksum
    item = session.get(QuestionBankItemVersion, uuid.UUID(identifiers["golden_item_version_id"]))
    assert item is not None and item.status is QuestionBankLifecycleStatus.ACTIVE
    assert item.content["correct_option_ids"] == ["a"]
    plan = CandidateRuntimeService._safe_plan(session, exam)
    delivered = plan["modules"][0]["sections"][0]["tasks"][0]["items"][0]
    assert delivered["item_version_id"] == str(item.id)
    assert GOLDEN_CONTENT_LABEL in delivered["content"]["prompt"]
    assert "Am Dienstag" in delivered["content"]["prompt"]
    assert [option["display_text"] for option in delivered["content"]["options"]] == [
        "Um 9 Uhr.",
        "Um 10 Uhr.",
    ]
    assert "correct_option_ids" not in delivered["content"]
    assert identifiers["historical_result_version_id"]
    return identifiers


def test_golden_seed_is_valid_deliverable_and_has_no_assigned_or_scored_journey(session) -> None:
    assert_golden_seed_preconditions(session)


def test_golden_reseeding_preserves_published_checksums_reviews_and_internal_policy(
    session,
) -> None:
    identifiers = assert_golden_seed_preconditions(session)
    from app.models import ExamVersion

    exam = session.get(ExamVersion, uuid.UUID(identifiers["golden_exam_version_id"]))
    pack = session.get(StandardPackVersion, uuid.UUID(identifiers["golden_pack_version_id"]))
    document = session.get(KnowledgeDocumentVersion, demo_id("golden-policy-document"))
    # Compare persisted values on both sides. SQLite unit fixtures remove the
    # timezone offset on read; comparing a freshly assigned aware value with a
    # reloaded naive value would assert serialization rather than immutability.
    session.flush()
    session.expire_all()
    before = (
        exam.checksum,
        exam.published_at,
        exam.activated_at,
        pack.configuration_checksum,
        pack.published_at,
        pack.approved_at,
        document.sha256,
        document.notes,
        document.approved_at,
        session.scalar(select(func.count()).select_from(HumanReviewRequest)),
    )
    assert reconcile_demo(session, _settings(), enabled=True) == identifiers
    session.expire_all()
    assert (
        exam.checksum,
        exam.published_at,
        exam.activated_at,
        pack.configuration_checksum,
        pack.published_at,
        pack.approved_at,
        document.sha256,
        document.notes,
        document.approved_at,
        session.scalar(select(func.count()).select_from(HumanReviewRequest)),
    ) == before


def test_reseeding_cannot_complete_approve_or_release_the_golden_candidate_journey(session) -> None:
    identifiers = assert_golden_seed_preconditions(session)
    organisation_id = uuid.UUID(identifiers["organisation_id"])
    administrator = session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
    reviewer = build_tenant_context(session, administrator.id, organisation_id)
    candidate = build_tenant_context(
        session, uuid.UUID(identifiers["golden_candidate_user_id"]), organisation_id
    )
    assignment = CandidateRuntimeService.create_assignment(
        session,
        reviewer,
        candidate_user_id=candidate.user_id,
        exam_version_id=uuid.UUID(identifiers["golden_exam_version_id"]),
    )
    attempt = CandidateRuntimeService.create_attempt(session, candidate, assignment.id)
    CandidateRuntimeService.start(session, candidate, attempt.id)
    binding_id = demo_id("golden-exam-binding")
    for revision, option in enumerate(("b", "a")):
        CandidateRuntimeService.save_response(
            session,
            candidate,
            attempt.id,
            binding_id,
            payload={"option_id": option},
            client_operation_id=uuid.uuid4(),
            expected_revision=revision,
        )
    CandidateRuntimeService.submit(session, candidate, attempt.id)
    run = ScoringService.score(session, reviewer, attempt.id)
    result = session.scalar(select(ResultVersion).where(ResultVersion.scoring_run_id == run.id))
    assert result is not None
    assert result.status is ResultVersionStatus.REVIEW_REQUIRED
    assert result.raw_awarded_points == 1 and result.raw_maximum_points == 1
    checksums = (run.checksum, result.checksum)
    session.commit()
    assert reconcile_demo(session, _settings(), enabled=True) == identifiers
    session.expire_all()
    assert attempt.status is CandidateAttemptStatus.SUBMITTED
    assert result.status is ResultVersionStatus.REVIEW_REQUIRED
    assert (run.checksum, result.checksum) == checksums
    assert (
        session.scalar(
            select(func.count())
            .select_from(CandidateExamAssignment)
            .where(CandidateExamAssignment.candidate_user_id == candidate.user_id)
        )
        == 1
    )
    revisions = list(
        session.scalars(
            select(CandidateResponseRevision)
            .where(CandidateResponseRevision.attempt_id == attempt.id)
            .order_by(CandidateResponseRevision.revision_number)
        )
    )
    assert [row.payload for row in revisions] == [{"option_id": "b"}, {"option_id": "a"}]
    assert (
        session.scalar(select(ResultRelease.id).where(ResultRelease.result_version_id == result.id))
        is None
    )
    assert (
        session.scalar(
            select(StatementOfResults.id).where(StatementOfResults.result_version_id == result.id)
        )
        is None
    )


def test_golden_seed_refuses_to_claim_an_existing_unrelated_email(session) -> None:
    existing = User(
        email=GOLDEN_CANDIDATE_EMAIL,
        normalized_email=GOLDEN_CANDIDATE_EMAIL,
        display_name="Unrelated existing identity",
        status=UserStatus.DISABLED,
    )
    session.add(existing)
    session.flush()
    credential = AuthenticationService(_settings()).set_password(
        session, existing, "Unrelated-original-password-2026!"
    )
    session.commit()
    original_hash = credential.password_hash
    with pytest.raises(RuntimeError, match="Golden demo identity collision"):
        reconcile_demo(session, _settings(), enabled=True)
    session.expire_all()
    assert existing.status is UserStatus.DISABLED
    assert existing.display_name == "Unrelated existing identity"
    assert credential.password_hash == original_hash
    assert (
        session.scalar(
            select(OrganisationMembership.id).where(OrganisationMembership.user_id == existing.id)
        )
        is None
    )
