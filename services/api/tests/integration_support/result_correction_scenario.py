from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.exams.service import ExamStudioService
from app.models import (
    AssessmentResult,
    CandidateAudioRecordingSession,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponse,
    CandidateResponseRevision,
    Exam,
    ExamVersion,
    ExamVersionItemBinding,
    ExamVersionModule,
    ExamVersionSection,
    ExamVersionTask,
    Organisation,
    OrganisationMembership,
    QuestionBankItem,
    QuestionBankItemVersion,
    ResultAppeal,
    ResultAppealVersion,
    ResultVersion,
    RubricVersion,
    ScoringRun,
    SpeechTranscription,
    SpeechTranscriptionVersion,
    StandardPack,
    StandardPackVersion,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
    User,
)
from app.models.enums import (
    AssessmentClassification,
    AssessmentFamily,
    CandidateAssignmentStatus,
    CandidateAttemptStatus,
    CandidateMediaStatus,
    ExamOriginType,
    ExamVersionStatus,
    OrganisationStatus,
    OrganisationType,
    PackMediaRequirement,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankOriginType,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankSkill,
    QuestionBankTaskType,
    ResultOutcome,
    ResultVersionStatus,
    RubricVersionStatus,
    ScoringRunStatus,
    StandardPackStatus,
    SubjectiveAssessmentStatus,
    TranscriptionStatus,
    UserStatus,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _checksum(value: Any) -> str:
    return hashlib.sha256(repr(value).encode()).hexdigest()


@dataclass
class GovernedResultCorrectionScenario:
    organisation: Organisation
    candidate: User
    second_candidate: User
    reviewer: User
    standard_pack_version: StandardPackVersion
    exam: Exam
    exam_version: ExamVersion
    objective_item: QuestionBankItem
    objective_item_version_i1: QuestionBankItemVersion
    objective_item_version_i2: QuestionBankItemVersion
    attempt: CandidateExamAttempt
    candidate_response: CandidateResponse
    candidate_response_revision: CandidateResponseRevision
    rubric_version: RubricVersion
    candidate_media_asset: CandidateMediaAsset
    speech_transcription: SpeechTranscription
    transcription_t1: SpeechTranscriptionVersion
    speaking_assessment: SubjectiveAssessment
    speaking_version_sp1: SubjectiveAssessmentVersion
    scoring_run_s1: ScoringRun
    assessment_result: AssessmentResult
    result_version_r1: ResultVersion
    appeal: ResultAppeal
    appeal_version: ResultAppealVersion


def build_governed_result_correction_scenario(
    session: Session, *, include_objective_replacement: bool = True
) -> GovernedResultCorrectionScenario:
    """Bootstrap one migration-backed, internally linked correction scenario.

    This builder intentionally seeds historical prerequisite state directly;
    correction behavior itself remains exercised through production services.
    """
    now = _now()
    org = Organisation(
        name=f"Correction Fixture {uuid.uuid4().hex[:8]}",
        slug=f"correction-{uuid.uuid4().hex[:12]}",
        organisation_type=OrganisationType.LANGUAGE_SCHOOL,
        status=OrganisationStatus.ACTIVE,
    )
    candidate = User(
        email=f"candidate-{uuid.uuid4()}@fixture.invalid",
        normalized_email=f"candidate-{uuid.uuid4()}@fixture.invalid",
        display_name="Candidate B",
        status=UserStatus.ACTIVE,
    )
    second_candidate = User(
        email=f"candidate-a-{uuid.uuid4()}@fixture.invalid",
        normalized_email=f"candidate-a-{uuid.uuid4()}@fixture.invalid",
        display_name="Candidate A",
        status=UserStatus.ACTIVE,
    )
    reviewer = User(
        email=f"reviewer-{uuid.uuid4()}@fixture.invalid",
        normalized_email=f"reviewer-{uuid.uuid4()}@fixture.invalid",
        display_name="Reviewer R1",
        status=UserStatus.ACTIVE,
    )
    session.add_all([org, candidate, second_candidate, reviewer])
    session.flush()
    session.add_all(
        [
            OrganisationMembership(organisation_id=org.id, user_id=candidate.id),
            OrganisationMembership(organisation_id=org.id, user_id=second_candidate.id),
            OrganisationMembership(organisation_id=org.id, user_id=reviewer.id),
        ]
    )
    pack = StandardPack(
        code=f"FIXTURE-{uuid.uuid4().hex[:8]}",
        name="Synthetic correction pack",
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        status=StandardPackStatus.ACTIVE,
    )
    pack_version = StandardPackVersion(
        standard_pack=pack,
        version_label="1",
        status=StandardPackStatus.ACTIVE,
        created_at=now,
        configuration_checksum=_checksum("pack"),
        supported_levels=["B1"],
        allowed_classifications=[AssessmentClassification.PRACTICE.value],
    )
    session.add(pack)
    session.flush()
    item = QuestionBankItem(
        code=f"ITEM-{uuid.uuid4().hex[:8]}",
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=org.id,
        status=QuestionBankLifecycleStatus.PUBLISHED,
        created_by_user_id=reviewer.id,
    )
    i1 = QuestionBankItemVersion(
        item=item,
        version_label="1",
        status=QuestionBankLifecycleStatus.PUBLISHED,
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        skill=QuestionBankSkill.SPEAKING,
        task_type=QuestionBankTaskType.MULTIPLE_CHOICE,
        response_mode=PackResponseMode.SELECT_ONE,
        media_requirement=PackMediaRequirement.NONE,
        content={"correct_option_ids": ["a"]},
        scoring_policy=QuestionBankScoringPolicy.OBJECTIVE_KEYED,
        max_points=Decimal("1"),
        scoring_metadata={"unanswered_policy": {"type": "ZERO", "points": "0"}},
        rights_status=QuestionBankRightsStatus.PRUFAI_ORIGINAL,
        origin_type=QuestionBankOriginType.HUMAN_AUTHORED,
        created_at=now,
        checksum=_checksum("i1"),
    )
    i2 = QuestionBankItemVersion(
        item=item,
        version_label="2",
        status=QuestionBankLifecycleStatus.PUBLISHED,
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        skill=QuestionBankSkill.SPEAKING,
        task_type=QuestionBankTaskType.MULTIPLE_CHOICE,
        response_mode=PackResponseMode.SELECT_ONE,
        media_requirement=PackMediaRequirement.NONE,
        content={"correct_option_ids": ["b"]},
        scoring_policy=QuestionBankScoringPolicy.OBJECTIVE_KEYED,
        max_points=Decimal("1"),
        scoring_metadata={"unanswered_policy": {"type": "ZERO", "points": "0"}},
        rights_status=QuestionBankRightsStatus.PRUFAI_ORIGINAL,
        origin_type=QuestionBankOriginType.HUMAN_AUTHORED,
        created_at=now,
        checksum=_checksum("i2"),
    )
    assert i1.item is item
    assert i2.item is item
    assert i1.origin_type is QuestionBankOriginType.HUMAN_AUTHORED
    assert i2.origin_type is QuestionBankOriginType.HUMAN_AUTHORED
    assert i1.created_at is not None
    assert i2.created_at is not None
    assert i1.checksum
    assert i2.checksum
    exam = Exam(
        code=f"EXAM-{uuid.uuid4().hex[:8]}",
        name="Synthetic correction exam",
        scope_type="ORGANISATION_PRIVATE",
        organisation_id=org.id,
        created_at=now,
    )
    exam_version = ExamVersion(
        exam=exam,
        version_label="1",
        status=ExamVersionStatus.PUBLISHED,
        standard_pack_version_id=pack_version.id,
        classification=AssessmentClassification.PRACTICE,
        origin_type=ExamOriginType.HUMAN_AUTHORED,
        checksum=_checksum("exam"),
        created_at=now,
    )
    module = ExamVersionModule(
        exam_version=exam_version, standard_pack_module_id=uuid.uuid4(), ordinal=1
    )
    section = ExamVersionSection(module=module, standard_pack_section_id=uuid.uuid4(), ordinal=1)
    task = ExamVersionTask(section=section, standard_pack_task_spec_id=uuid.uuid4(), ordinal=1)
    binding = ExamVersionItemBinding(
        task=task,
        slot_index=0,
        question_bank_item_version_id=i1.id,
        origin_type=ExamOriginType.HUMAN_AUTHORED,
        created_at=now,
    )
    session.add_all([item, exam, module, section, task, binding])
    session.flush()
    assert i1.item_id is not None
    assert i2.item_id is not None
    ExamStudioService._refresh_checksum(session, exam_version)
    session.flush()
    assignment = CandidateExamAssignment(
        organisation_id=org.id,
        candidate_user_id=candidate.id,
        exam_version_id=exam_version.id,
        status=CandidateAssignmentStatus.COMPLETED,
        maximum_attempts=1,
        created_at=now,
    )
    attempt = CandidateExamAttempt(
        assignment=assignment,
        candidate_user_id=candidate.id,
        organisation_id=org.id,
        exam_version_id=exam_version.id,
        attempt_number=1,
        status=CandidateAttemptStatus.SUBMITTED,
        delivery_manifest_checksum=_checksum("manifest"),
        runtime_plan_checksum=_checksum("runtime"),
        runtime_plan={"standard_pack_version_id": str(pack_version.id)},
        submitted_at=now,
        created_at=now,
    )
    response = CandidateResponse(
        attempt=attempt,
        exam_version_item_binding_id=binding.id,
        current_revision=1,
        current_payload={"option_id": "b"},
        finalized_at=now,
    )
    revision = CandidateResponseRevision(
        response=response,
        attempt_id=attempt.id,
        revision_number=1,
        client_operation_id=uuid.uuid4(),
        payload={"option_id": "b"},
        payload_checksum=_checksum("response"),
        received_at=now,
    )
    session.add_all([assignment, attempt, response, revision])
    session.flush()
    session.add(
        CandidateAudioRecordingSession(
            attempt_id=attempt.id,
            exam_version_item_binding_id=binding.id,
            candidate_user_id=candidate.id,
            organisation_id=org.id,
            attempt_number=1,
            status=CandidateMediaStatus.READY,
            operation_id=uuid.uuid4(),
            policy_snapshot={},
        )
    )
    session.flush()
    recording = session.query(CandidateAudioRecordingSession).filter_by(attempt_id=attempt.id).one()
    audio = b"synthetic correction audio"
    asset = CandidateMediaAsset(
        organisation_id=org.id,
        candidate_user_id=candidate.id,
        attempt_id=attempt.id,
        exam_version_item_binding_id=binding.id,
        recording_session_id=recording.id,
        content_type="audio/ogg",
        byte_size=len(audio),
        sha256=hashlib.sha256(audio).hexdigest(),
        storage_key=f"fixture/{uuid.uuid4()}",
        status=CandidateMediaStatus.READY,
        created_at=now,
    )
    transcription = SpeechTranscription(
        asset=asset, operation_id=uuid.uuid4(), language="de", status=TranscriptionStatus.READY
    )
    t1 = SpeechTranscriptionVersion(
        transcription=transcription,
        version_number=1,
        candidate_media_asset_id=asset.id,
        audio_sha256=asset.sha256,
        transcript="synthetic transcript",
        segments=[],
        checksum=_checksum("t1"),
        status=TranscriptionStatus.READY,
        language="de",
        configuration={},
        created_at=now,
    )
    session.add_all([asset, transcription, t1])
    session.flush()
    rubric = RubricVersion(
        rubric_id=uuid.uuid4(),
        version_label="1",
        schema_version="1",
        assessment_family=AssessmentFamily.LANGUAGE,
        language="de",
        cefr_levels=["B1"],
        skill=QuestionBankSkill.SPEAKING,
        standard_pack_version_id=pack_version.id,
        criteria=[{"code": "FLUENCY", "max_points": "1"}],
        performance_levels=[{"code": "A"}],
        aggregation_policy={"method": "SUM"},
        evidence_policy={},
        ai_assessment_policy={},
        human_review_policy={},
        status=RubricVersionStatus.PUBLISHED,
        checksum=_checksum("rubric"),
        created_at=now,
    )
    speaking = SubjectiveAssessment(
        attempt_id=attempt.id,
        exam_version_id=exam_version.id,
        exam_version_item_binding_id=binding.id,
        question_bank_item_version_id=i1.id,
        candidate_user_id=candidate.id,
        organisation_id=org.id,
        created_at=now,
    )
    sp1 = SubjectiveAssessmentVersion(
        assessment=speaking,
        version_number=1,
        attempt_id=attempt.id,
        exam_version_id=exam_version.id,
        exam_version_item_binding_id=binding.id,
        question_bank_item_version_id=i1.id,
        candidate_response_id=response.id,
        candidate_response_revision_id=revision.id,
        rubric_version_id=rubric.id,
        standard_pack_version_id=pack_version.id,
        status=SubjectiveAssessmentStatus.APPROVED,
        response_checksum=revision.payload_checksum,
        rubric_checksum=rubric.checksum,
        candidate_media_asset_id=asset.id,
        audio_sha256=asset.sha256,
        speech_transcription_version_id=t1.id,
        transcript_checksum=t1.checksum,
        evidence_mode_summary={},
        ai_proposal={},
        human_approved_values={"FLUENCY": {"points": "1"}},
        consistency_summary={},
        confidence_summary={},
        disagreement_summary={},
        provenance={},
        proposed_score=Decimal("1"),
        approved_score=Decimal("1"),
        checksum=_checksum("sp1"),
        created_at=now,
    )
    session.add_all([rubric, speaking, sp1])
    session.flush()
    run = ScoringRun(
        attempt_id=attempt.id,
        exam_version_id=exam_version.id,
        organisation_id=org.id,
        candidate_user_id=candidate.id,
        standard_pack_version_id=pack_version.id,
        status=ScoringRunStatus.COMPLETED,
        response_set_checksum=_checksum("response-set"),
        scoring_engine_name="fixture",
        scoring_engine_version="1",
        configuration_checksum=_checksum("config"),
        checksum=_checksum("s1"),
        started_at=now,
        completed_at=now,
        initiated_by_user_id=reviewer.id,
    )
    session.add(run)
    session.flush()
    result = AssessmentResult(
        attempt_id=attempt.id,
        candidate_user_id=candidate.id,
        organisation_id=org.id,
        exam_version_id=exam_version.id,
        created_at=now,
    )
    r1 = ResultVersion(
        result=result,
        scoring_run_id=run.id,
        version_number=1,
        status=ResultVersionStatus.RELEASED,
        response_set_checksum=run.response_set_checksum,
        scoring_run_checksum=run.checksum or "",
        raw_awarded_points=Decimal("1"),
        raw_maximum_points=Decimal("1"),
        outcome=ResultOutcome.PASS,
        rule_provenance={},
        checksum=_checksum("r1"),
        created_at=now,
        released_at=now,
        released_by_user_id=reviewer.id,
    )
    session.add_all([result, r1])
    session.flush()
    appeal = ResultAppeal(
        organisation_id=org.id,
        candidate_user_id=candidate.id,
        attempt_id=attempt.id,
        result_id=result.id,
        challenged_result_version_id=r1.id,
        scoring_run_id=run.id,
        exam_version_id=exam_version.id,
        standard_pack_version_id=pack_version.id,
        challenged_result_checksum=r1.checksum,
        status="SUBMITTED",
        category="CORRECTION",
        operation_id=str(uuid.uuid4()),
        current_version_number=1,
        current_checksum=_checksum("appeal"),
        created_at=now,
        updated_at=now,
    )
    av = ResultAppealVersion(
        appeal=appeal,
        version_number=1,
        status="SUBMITTED",
        category="CORRECTION",
        grounds="synthetic",
        challenged_result_version_id=r1.id,
        challenged_result_checksum=r1.checksum,
        evidence_bundle_checksum=_checksum("evidence"),
        operation_id=str(uuid.uuid4()),
        actor_user_id=candidate.id,
        checksum=_checksum("appeal-version"),
        created_at=now,
    )
    session.add_all([appeal, av])
    session.flush()
    return GovernedResultCorrectionScenario(
        org,
        candidate,
        second_candidate,
        reviewer,
        pack_version,
        exam,
        exam_version,
        item,
        i1,
        i2,
        attempt,
        response,
        revision,
        rubric,
        asset,
        transcription,
        t1,
        speaking,
        sp1,
        run,
        result,
        r1,
        appeal,
        av,
    )
