"""Resolve controlled feedback receipts without copying private source evidence."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, cast

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.authorization.membership import require_active_tenant_permission
from app.evaluation import service as evaluation
from app.evaluation.contracts import Contract
from app.evaluation.regression_registry import TECHNICAL_FAILURES, measured_failure_keys
from app.feedback.common import FeedbackConflict, Pin, require_platform
from app.models import (
    AuditEvent,
    CandidateExamAttempt,
    CandidateResponseRevision,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationRun,
    ExamVersion,
    ExamVersionItemBinding,
    QuestionBankItemVersion,
    ResultAppeal,
    ResultAppealVersion,
    ResultVersion,
    RubricVersion,
    ScoringRun,
    SpeakingExecutionJob,
    SpeechTranscriptionJob,
    StandardPackVersion,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
    WritingExecutionJob,
)
from app.models.enums import ActorType, AppealDecision, AppealStatus
from app.models.evaluation import REGRESSION_ADAPTER
from app.result_governance.service import _checksum as appeal_checksum
from app.writing_assessment.service import RubricService, canonical_checksum


class EvaluationFailure(Contract):
    kind: Literal["EVALUATION_FAILURE"]
    run: Pin
    result: Pin
    failed_check: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")


class ReviewerFlag(Contract):
    kind: Literal["REVIEWER_FLAG"]
    artifact_type: Literal["SUBJECTIVE_ASSESSMENT_VERSION"]
    artifact: Pin


class AppealOutcome(Contract):
    kind: Literal["GOVERNED_APPEAL_OUTCOME"]
    appeal_version: Pin


class OperationalFailure(Contract):
    kind: Literal["OPERATIONAL_FAILURE"]
    event_id: uuid.UUID


FeedbackSource = Annotated[
    EvaluationFailure | ReviewerFlag | AppealOutcome | OperationalFailure,
    Field(discriminator="kind"),
]


@dataclass(frozen=True)
class ResolvedSource:
    organisation_id: uuid.UUID | None
    evidence_class: str
    body: dict[str, Any]

    @property
    def privacy_class(self) -> str:
        return "INSTITUTION_RESTRICTED" if self.organisation_id else "PLATFORM_METADATA"


ARTIFACT_FIELDS = (
    ("STANDARD_PACK_VERSION", "standard_pack_version_id", "standard_pack_checksum"),
    ("RUBRIC_VERSION", "rubric_version_id", "rubric_checksum"),
    ("QUESTION_BANK_ITEM_VERSION", "question_bank_item_version_id", "item_checksum"),
    ("EXAM_VERSION", "exam_version_id", "exam_version_checksum"),
    ("PROMPT_VERSION", "prompt_version_id", "prompt_checksum"),
    ("WORKFLOW_VERSION", "workflow_version_id", "workflow_checksum"),
    ("MODEL_PROFILE", "model_profile_id", "model_version"),
    ("TRANSCRIPTION_PROFILE", "profile_id", "profile_version"),
)
REVIEW_STATES = {
    "AI_PROPOSED",
    "REVIEW_REQUIRED",
    "IN_REVIEW",
    "APPROVED",
    "SUPERSEDED",
    "REJECTED",
    "CHANGES_REQUESTED",
}
FINAL_APPEALS = {
    AppealDecision.UPHOLD: AppealStatus.ADJUDICATED_UPHELD,
    AppealDecision.PARTIALLY_UPHOLD: AppealStatus.ADJUDICATED_PARTIALLY_UPHELD,
    AppealDecision.REJECT: AppealStatus.ADJUDICATED_REJECTED,
}
JOB_EVENTS = {
    ("writing_execution.failed", "WRITING_EXECUTION_JOB"): (WritingExecutionJob, "WRITING"),
    ("speaking_execution.failed", "SPEAKING_EXECUTION_JOB"): (SpeakingExecutionJob, "SPEAKING"),
    ("speech_transcription.failed", "SPEECH_TRANSCRIPTION_JOB"): (
        SpeechTranscriptionJob,
        "TRANSCRIPTION",
    ),
}
JOBS_BY_SKILL = {skill: model for model, skill in JOB_EVENTS.values()}
ExecutionJob = WritingExecutionJob | SpeakingExecutionJob | SpeechTranscriptionJob


def authorize_scope(
    session: Session,
    context: TenantContext,
    organisation_id: uuid.UUID | None,
    read_permission: str | None,
    *,
    mode: Literal["read", "intake", "decide"] = "read",
) -> TenantContext:
    if context.organisation_id != organisation_id:
        raise AuthorizationError("FEEDBACK_SOURCE_SCOPE_REQUIRED")
    if organisation_id is None:
        permissions = {
            "read": (evaluation.READ,),
            "intake": (evaluation.READ, evaluation.RUN, evaluation.MANAGE),
            "decide": (evaluation.READ, evaluation.MANAGE, evaluation.REVIEW),
        }[mode]
        return require_platform(session, context, *permissions)
    trusted = build_tenant_context(session, context.user_id, organisation_id)
    if read_permission is None:
        raise AuthorizationError("FEEDBACK_SOURCE_SCOPE_REQUIRED")
    require_active_tenant_permission(session, trusted, read_permission, organisation_id)
    if read_permission == "subjective.review":
        require_active_tenant_permission(session, trusted, "subjective.read", organisation_id)
    if mode == "decide" and read_permission == "appeal.read":
        require_active_tenant_permission(session, trusted, "appeal.adjudicate", organisation_id)
    if mode == "decide" and read_permission == "speech_transcription.read":
        require_active_tenant_permission(
            session, trusted, "speech_transcription.request", organisation_id
        )
    return trusted


def _pins(values: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"type": kind, **Pin(id=values[id_key], checksum=values[sha_key]).model_dump(mode="json")}
        for kind, id_key, sha_key in ARTIFACT_FIELDS
        if values.get(id_key) is not None and values.get(sha_key) is not None
    ]


def _body(
    *,
    source_id: uuid.UUID,
    checksum: str,
    family: str,
    skill: str,
    method: str,
    pins: list[dict[str, str]],
    permission: str | None,
    reference: bool,
    execution_class: str = "NOT_RUN",
    **details: Any,
) -> dict[str, Any]:
    return {
        "source_id": str(source_id),
        "source_checksum": checksum,
        "assessment_family": family,
        "skill": skill,
        "method": method,
        "artifact_pins": pins,
        "requires_reference": reference,
        "execution_class": execution_class,
        "read_permission": permission,
        **details,
    }


def _measured_failure(row: EvaluationCaseResult, run: EvaluationRun, check: str) -> bool:
    result = row.result
    if result.get("hard_fail") is not True or check not in TECHNICAL_FAILURES:
        return False
    if run.configuration.get("execution_adapter") != REGRESSION_ADAPTER:
        return check in measured_failure_keys(result)
    finding = result.get("regression_case", {})
    if finding.get("candidate_status") != "FAIL":
        return False
    for check_row in finding.get("checks", []):
        if check_row.get("code") != check:
            continue
        measured = check_row.get("candidate", {})
        numerator, denominator = measured.get("numerator"), measured.get("denominator")
        return (
            measured.get("status") == "FAIL"
            and type(numerator) is int
            and type(denominator) is int
            and 0 <= numerator < denominator
            and bool(TECHNICAL_FAILURES[check].intersection(result.get("failure_categories", [])))
        )
    return False


def _authorized_replay_job(
    session: Session, context: TenantContext, skill: str, receipt: dict[str, Any]
) -> ExecutionJob | None:
    execution = receipt.get("execution_job_id")
    if execution is None:
        if receipt.get("adapter") != "SYNTHETIC_OBSERVATION_REPLAY_V1":
            raise FeedbackConflict("FEEDBACK_EVALUATION_EXECUTION_PIN_MISMATCH")
        return None
    model = JOBS_BY_SKILL.get(skill)
    job = cast(ExecutionJob | None, session.get(model, uuid.UUID(execution))) if model else None
    if job is None or job.snapshot_checksum != receipt.get("snapshot_checksum"):
        raise FeedbackConflict("FEEDBACK_EVALUATION_EXECUTION_PIN_MISMATCH")
    if job.organisation_id != context.organisation_id:
        raise AuthorizationError("FEEDBACK_SOURCE_SCOPE_REQUIRED")
    if job.candidate_user_id == context.user_id:
        raise AuthorizationError("FEEDBACK_EVALUATION_CANDIDATE_DENIED")
    return job


def authorize_evaluation_receipt(
    session: Session, context: TenantContext, body: dict[str, Any]
) -> None:
    primary = session.get(EvaluationCaseResult, uuid.UUID(body["primary_result_id"]))
    if (
        primary is None
        or primary.checksum != body["primary_result_checksum"]
        or canonical_checksum(
            {
                "input_checksum": primary.input_checksum,
                "observation_checksum": primary.observation_checksum,
                "execution_reference": primary.execution_reference,
                "result": primary.result,
            }
        )
        != primary.checksum
    ):
        raise FeedbackConflict("FEEDBACK_EVALUATION_RESULT_PIN_MISMATCH")
    _authorized_replay_job(session, context, body["skill"], primary.execution_reference)


def _replay_artifacts(job: ExecutionJob | None, values: dict[str, Any]) -> dict[str, Any]:
    if job is None:
        return values
    if isinstance(job, SpeechTranscriptionJob):
        return {**values, "profile_id": job.profile_id}
    snapshot = job.execution_snapshot
    if (
        canonical_checksum(snapshot) != job.snapshot_checksum
        or snapshot.get("prompt_version_id") != values.get("prompt_version_id")
        or snapshot.get("workflow_version_id") != values.get("workflow_version_id")
    ):
        raise FeedbackConflict("FEEDBACK_EVALUATION_EXECUTION_PIN_MISMATCH")
    return {
        **values,
        "prompt_checksum": snapshot.get("prompt_checksum"),
        "workflow_checksum": snapshot.get("workflow_checksum"),
    }


def _evaluation_source(
    session: Session, context: TenantContext, source: EvaluationFailure
) -> ResolvedSource:
    run = session.get(EvaluationRun, source.run.id)
    if run is None:
        raise FeedbackConflict("FEEDBACK_EVALUATION_RUN_PIN_MISMATCH")
    dataset = evaluation._version(session, run.dataset_version_id)
    owner = session.get(EvaluationDataset, dataset.dataset_id)
    if owner is None:
        raise FeedbackConflict("FEEDBACK_EVALUATION_RUN_PIN_MISMATCH")
    permission = (
        "speech_transcription.read" if dataset.skill == "TRANSCRIPTION" else "subjective.review"
    )
    trusted = authorize_scope(session, context, owner.organisation_id, permission, mode="intake")
    if (
        run.status != "COMPLETED"
        or run.completed_at is None
        or run.hard_gate != "FAIL"
        or run.checksum != source.run.checksum
    ):
        raise FeedbackConflict("FEEDBACK_EVALUATION_RUN_PIN_MISMATCH")
    split = run.configuration.get("dataset_split")
    if split == "HELD_OUT" or dataset.provenance.get("split") == "HELD_OUT":
        raise AuthorizationError("FEEDBACK_HELD_OUT_UNAVAILABLE")
    rows = evaluation.verify_run(session, run)
    result = next((row for row in rows if row.id == source.result.id), None)
    if result is None or result.checksum != source.result.checksum:
        raise FeedbackConflict("FEEDBACK_EVALUATION_RESULT_PIN_MISMATCH")
    if not _measured_failure(result, run, source.failed_check):
        raise ValueError("FEEDBACK_MEASURED_FAILURE_REQUIRED")
    config = run.configuration
    values = config
    primary = result
    if config.get("execution_adapter") == REGRESSION_ADAPTER:
        finding = result.result["regression_case"]
        component = finding["candidate_execution"]
        candidate_run = session.get(EvaluationRun, uuid.UUID(component["run"]["id"]))
        candidate_result = session.get(
            EvaluationCaseResult, uuid.UUID(finding["candidate_result"]["id"])
        )
        if (
            candidate_run is None
            or candidate_run.checksum != component["run"]["checksum"]
            or candidate_result is None
            or candidate_result.run_id != candidate_run.id
            or candidate_result.checksum != finding["candidate_result"]["checksum"]
        ):
            raise FeedbackConflict("FEEDBACK_EVALUATION_RESULT_PIN_MISMATCH")
        values = candidate_run.configuration
        primary = candidate_result
    job = _authorized_replay_job(session, trusted, dataset.skill, primary.execution_reference)
    values = _replay_artifacts(job, values)
    if values.get("rubric_version_id"):
        rubric = session.get(RubricVersion, uuid.UUID(values["rubric_version_id"]))
        if rubric is None or rubric.rubric.organisation_id not in {None, owner.organisation_id}:
            raise AuthorizationError("FEEDBACK_SOURCE_SCOPE_REQUIRED")
    return ResolvedSource(
        owner.organisation_id,
        run.evidence_class,
        _body(
            source_id=result.id,
            checksum=result.checksum,
            family=dataset.assessment_family,
            skill=dataset.skill,
            method=str(config["implementation_version"]),
            pins=_pins(values),
            permission=permission if owner.organisation_id else None,
            reference=False,
            execution_class=run.execution_class,
            run_id=str(run.id),
            run_checksum=run.checksum,
            result_id=str(result.id),
            result_checksum=result.checksum,
            primary_result_id=str(primary.id),
            primary_result_checksum=primary.checksum,
            dataset_version_id=str(dataset.id),
            dataset_checksum=dataset.checksum,
            split=split,
            language=dataset.language,
            cefr_level=dataset.cefr_level,
            evaluation_method=config["evaluation_method"],
            evaluation_method_version=config["evaluation_method_version"],
            failed_check=source.failed_check,
            source_actor_id=str(run.created_by),
            source_operation_id=str(run.operation_id),
        ),
    )


def _assessment_source(
    session: Session, context: TenantContext, source: ReviewerFlag
) -> ResolvedSource:
    version = session.get(SubjectiveAssessmentVersion, source.artifact.id)
    if version is None:
        raise FeedbackConflict("FEEDBACK_REVIEW_VERSION_PIN_MISMATCH")
    assessment = session.get(SubjectiveAssessment, version.subjective_assessment_id)
    if assessment is None:
        raise FeedbackConflict("FEEDBACK_REVIEW_VERSION_PIN_MISMATCH")
    trusted = authorize_scope(
        session, context, assessment.organisation_id, "subjective.review", mode="intake"
    )
    if trusted.user_id == assessment.candidate_user_id:
        raise AuthorizationError("FEEDBACK_REVIEWER_CANDIDATE_DENIED")
    if version.checksum != source.artifact.checksum or version.status.value not in REVIEW_STATES:
        raise FeedbackConflict("FEEDBACK_REVIEW_VERSION_PIN_MISMATCH")
    rubric = session.get(RubricVersion, version.rubric_version_id)
    exam = session.get(ExamVersion, version.exam_version_id)
    item = session.get(QuestionBankItemVersion, version.question_bank_item_version_id)
    pack = session.get(StandardPackVersion, version.standard_pack_version_id)
    revision = session.get(CandidateResponseRevision, version.candidate_response_revision_id)
    binding = session.get(ExamVersionItemBinding, version.exam_version_item_binding_id)
    attempt = session.get(CandidateExamAttempt, version.attempt_id)
    if (
        rubric is None
        or exam is None
        or item is None
        or pack is None
        or revision is None
        or binding is None
        or attempt is None
        or rubric.skill.value != "WRITING"
        or rubric.rubric.organisation_id not in {None, assessment.organisation_id}
        or exam.exam.organisation_id not in {None, assessment.organisation_id}
        or item.item.organisation_id not in {None, assessment.organisation_id}
        or rubric.checksum != version.rubric_checksum
        or canonical_checksum(RubricService.checksum_payload(rubric)) != rubric.checksum
        or attempt.organisation_id != assessment.organisation_id
        or attempt.candidate_user_id != assessment.candidate_user_id
        or attempt.exam_version_id != exam.id
        or attempt.status.value != "SUBMITTED"
        or version.attempt_id != assessment.attempt_id
        or version.exam_version_id != assessment.exam_version_id
        or version.exam_version_item_binding_id != assessment.exam_version_item_binding_id
        or version.question_bank_item_version_id != assessment.question_bank_item_version_id
        or exam.standard_pack_version_id != pack.id
        or rubric.standard_pack_version_id != pack.id
        or binding.question_bank_item_version_id != item.id
        or binding.task.section.module.exam_version_id != exam.id
        or revision.attempt_id != attempt.id
        or revision.candidate_response_id != version.candidate_response_id
        or revision.response.exam_version_item_binding_id != binding.id
        or revision.payload_checksum != version.response_checksum
        or canonical_checksum(revision.payload) != version.response_checksum
    ):
        raise FeedbackConflict("FEEDBACK_REVIEW_SOURCE_PROVENANCE_INVALID")
    job = session.scalar(
        select(WritingExecutionJob).where(WritingExecutionJob.assessment_version_id == version.id)
    )
    snapshot: dict[str, Any] = {}
    if job is not None:
        if (
            job.organisation_id != assessment.organisation_id
            or job.candidate_response_revision_id != revision.id
            or job.rubric_checksum != rubric.checksum
            or canonical_checksum(job.execution_snapshot) != job.snapshot_checksum
        ):
            raise FeedbackConflict("FEEDBACK_REVIEW_SOURCE_PROVENANCE_INVALID")
        snapshot = job.execution_snapshot
    test_only = snapshot.get("provider") == "DETERMINISTIC_TEST"
    return ResolvedSource(
        assessment.organisation_id,
        "TECHNICAL_TEST" if test_only else "UNLABELLED_OBSERVATION",
        _body(
            source_id=version.id,
            checksum=version.checksum,
            family=rubric.assessment_family.value,
            skill=rubric.skill.value,
            method="SUBJECTIVE_REVIEW",
            pins=_pins(
                {
                    **snapshot,
                    "standard_pack_version_id": pack.id,
                    "standard_pack_checksum": pack.configuration_checksum,
                    "rubric_version_id": rubric.id,
                    "rubric_checksum": rubric.checksum,
                    "question_bank_item_version_id": item.id,
                    "item_checksum": item.checksum,
                    "exam_version_id": exam.id,
                    "exam_version_checksum": exam.checksum,
                }
            ),
            permission="subjective.review",
            reference=True,
            execution_class="TEST_ONLY" if test_only else "NOT_RUN",
            language=rubric.language,
            source_actor_id=str(trusted.user_id),
        ),
    )


def _appeal_source(
    session: Session, context: TenantContext, source: AppealOutcome
) -> ResolvedSource:
    version = session.get(ResultAppealVersion, source.appeal_version.id)
    appeal = session.get(ResultAppeal, version.appeal_id) if version else None
    if version is None or appeal is None:
        raise FeedbackConflict("FEEDBACK_APPEAL_DECISION_PIN_MISMATCH")
    trusted = authorize_scope(
        session, context, appeal.organisation_id, "appeal.read", mode="intake"
    )
    if trusted.user_id == appeal.candidate_user_id:
        raise AuthorizationError("FEEDBACK_APPEAL_CANDIDATE_DENIED")
    if (
        version.checksum != source.appeal_version.checksum
        or version.decision not in FINAL_APPEALS
        or FINAL_APPEALS[version.decision] != version.status
        or appeal.current_version_number != version.version_number
        or appeal.current_checksum != version.checksum
        or version.actor_user_id == appeal.candidate_user_id
    ):
        raise FeedbackConflict("FEEDBACK_APPEAL_DECISION_PIN_MISMATCH")
    predecessor = session.scalar(
        select(ResultAppealVersion).where(
            ResultAppealVersion.appeal_id == appeal.id,
            ResultAppealVersion.version_number == version.version_number - 1,
        )
    )
    result = session.get(ResultVersion, appeal.challenged_result_version_id)
    run = session.get(ScoringRun, appeal.scoring_run_id)
    pack = session.get(StandardPackVersion, appeal.standard_pack_version_id)
    exam = session.get(ExamVersion, appeal.exam_version_id)
    if (
        predecessor is None
        or result is None
        or run is None
        or pack is None
        or exam is None
        or predecessor.evidence_bundle_checksum != version.evidence_bundle_checksum
        or version.challenged_result_version_id != result.id
        or version.challenged_result_checksum != result.checksum
        or predecessor.challenged_result_version_id != result.id
        or predecessor.challenged_result_checksum != result.checksum
        or appeal.challenged_result_checksum != result.checksum
        or result.result_id != appeal.result_id
        or result.scoring_run_id != run.id
        or result.scoring_run_checksum != run.checksum
        or run.organisation_id != appeal.organisation_id
        or run.attempt_id != appeal.attempt_id
        or run.candidate_user_id != appeal.candidate_user_id
        or run.exam_version_id != exam.id
        or run.standard_pack_version_id != pack.id
        or version.checksum
        != appeal_checksum(
            {
                "appeal": predecessor.checksum,
                "decision": version.decision.value,
                "reason": version.decision_reason,
            }
        )
    ):
        raise FeedbackConflict("FEEDBACK_APPEAL_DECISION_PROVENANCE_INVALID")
    receipts = list(
        session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "appeal.adjudicated",
                AuditEvent.entity_type == "result_appeal",
                AuditEvent.entity_id == str(appeal.id),
                AuditEvent.entity_version == str(version.version_number),
                AuditEvent.organisation_id == appeal.organisation_id,
                AuditEvent.actor_id == version.actor_user_id,
                AuditEvent.actor_type == ActorType.HUMAN,
            )
        )
    )
    if len(receipts) != 1 or receipts[0].metadata_json != {
        "decision": version.decision.value,
        "result_version_id": str(result.id),
    }:
        raise FeedbackConflict("FEEDBACK_APPEAL_DECISION_PROVENANCE_INVALID")
    return ResolvedSource(
        appeal.organisation_id,
        "UNLABELLED_OBSERVATION",
        _body(
            source_id=version.id,
            checksum=version.checksum,
            family=pack.standard_pack.assessment_family.value,
            skill="UNSPECIFIED",
            method="APPEAL_ADJUDICATION",
            pins=_pins(
                {
                    "standard_pack_version_id": pack.id,
                    "standard_pack_checksum": pack.configuration_checksum,
                    "exam_version_id": exam.id,
                    "exam_version_checksum": exam.checksum,
                }
            ),
            permission="appeal.read",
            reference=True,
            language=pack.standard_pack.language,
            decision=version.decision.value,
            appeal_id=str(appeal.id),
            challenged_result_version_id=str(result.id),
            challenged_result_checksum=result.checksum,
            scoring_run_id=str(run.id),
            scoring_run_checksum=run.checksum,
            adjudication_receipt_id=str(receipts[0].id),
            source_actor_id=str(version.actor_user_id),
            source_operation_checksum=canonical_checksum(version.operation_id),
        ),
    )


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _job_source(
    session: Session, context: TenantContext, source: OperationalFailure
) -> ResolvedSource:
    event = session.get(AuditEvent, source.event_id)
    if (
        event is None
        or event.organisation_id is None
        or event.actor_type != ActorType.SYSTEM_SERVICE
    ):
        raise FeedbackConflict("FEEDBACK_OPERATIONAL_FAILURE_UNAVAILABLE")
    if context.organisation_id != event.organisation_id:
        raise AuthorizationError("FEEDBACK_SOURCE_SCOPE_REQUIRED")
    route = JOB_EVENTS.get((event.event_type, event.entity_type))
    metadata = event.metadata_json or {}
    if (
        route is None
        or metadata.get("status") != "FAILED"
        or metadata.get("job_id") != event.entity_id
    ):
        raise FeedbackConflict("FEEDBACK_OPERATIONAL_FAILURE_UNAVAILABLE")
    model, skill = route
    permission = "speech_transcription.read" if skill == "TRANSCRIPTION" else "subjective.review"
    trusted = authorize_scope(session, context, event.organisation_id, permission, mode="intake")
    try:
        job_id = uuid.UUID(event.entity_id or "")
    except ValueError as exc:
        raise FeedbackConflict("FEEDBACK_OPERATIONAL_FAILURE_UNAVAILABLE") from exc
    job = cast(ExecutionJob | None, session.get(model, job_id))
    category = metadata.get("failure_category")
    count = metadata.get("attempt_count")
    if (
        job is None
        or job.organisation_id != event.organisation_id
        or event.actor_id != job.requested_by_user_id
        or trusted.user_id == job.candidate_user_id
        or not isinstance(category, str)
        or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", category)
        or type(count) is not int
        or count < 1
        or count > job.attempt_count
        or metadata.get("profile_version") != job.profile_version
    ):
        raise FeedbackConflict("FEEDBACK_OPERATIONAL_FAILURE_UNAVAILABLE")
    if not any(
        row.get("status") == "FAILED"
        and row.get("attempt_count") == count
        and row.get("failure_category") == category
        and _utc(datetime.fromisoformat(row["at"])) <= _utc(event.timestamp)
        for row in job.state_history
    ):
        raise FeedbackConflict("FEEDBACK_OPERATIONAL_FAILURE_UNAVAILABLE")
    if isinstance(job, SpeechTranscriptionJob):
        snapshot = job.profile_snapshot
        binding = session.get(ExamVersionItemBinding, job.exam_version_item_binding_id)
        exam = binding.task.section.module.exam_version if binding else None
        pack = session.get(StandardPackVersion, exam.standard_pack_version_id) if exam else None
        values = {"profile_id": job.profile_id, "profile_version": job.profile_version}
        if metadata.get("speech_transcription_id") != str(job.transcription_id):
            raise FeedbackConflict("FEEDBACK_OPERATIONAL_FAILURE_UNAVAILABLE")
    else:
        snapshot = job.execution_snapshot
        pack = session.get(StandardPackVersion, job.standard_pack_version_id)
        values = {
            **snapshot,
            "rubric_version_id": job.rubric_version_id,
            "rubric_checksum": job.rubric_checksum,
        }
        if metadata.get("ai_run_id") != str(job.ai_run_id):
            raise FeedbackConflict("FEEDBACK_OPERATIONAL_FAILURE_UNAVAILABLE")
    if pack is None or canonical_checksum(snapshot) != job.snapshot_checksum:
        raise FeedbackConflict("FEEDBACK_OPERATIONAL_FAILURE_UNAVAILABLE")
    test_only = snapshot.get("provider") == "DETERMINISTIC_TEST"
    return ResolvedSource(
        event.organisation_id,
        "TECHNICAL_TEST" if test_only else "UNLABELLED_OBSERVATION",
        _body(
            source_id=event.id,
            checksum=canonical_checksum(
                {
                    "event_id": str(event.id),
                    "job_id": str(job.id),
                    "snapshot_checksum": job.snapshot_checksum,
                    "failure_category": category,
                    "attempt_count": count,
                }
            ),
            family=pack.standard_pack.assessment_family.value,
            skill=skill,
            method=event.event_type,
            pins=_pins(
                {
                    **values,
                    "standard_pack_version_id": pack.id,
                    "standard_pack_checksum": pack.configuration_checksum,
                }
            ),
            permission=permission,
            reference=False,
            execution_class="TEST_ONLY" if test_only else "UNESTABLISHED",
            language=pack.standard_pack.language,
            job_id=str(job.id),
            failure_category=category,
            attempt_count=count,
            source_actor_id=str(job.requested_by_user_id),
            source_operation_id=str(job.operation_id),
        ),
    )


def resolve_source(
    session: Session, context: TenantContext, source: FeedbackSource
) -> ResolvedSource:
    if isinstance(source, EvaluationFailure):
        return _evaluation_source(session, context, source)
    if isinstance(source, ReviewerFlag):
        return _assessment_source(session, context, source)
    if isinstance(source, AppealOutcome):
        return _appeal_source(session, context, source)
    return _job_source(session, context, source)


def operational_candidate_id(session: Session, body: dict[str, Any]) -> uuid.UUID | None:
    model = JOBS_BY_SKILL.get(body["skill"])
    if model is None:
        return None
    job = cast(ExecutionJob | None, session.get(model, uuid.UUID(body["job_id"])))
    return job.candidate_user_id if job else None
