"""Read existing execution evidence; never invoke providers or create assessments.

Platform evaluation authority is insufficient for institution-private evidence.
The existing execution services remain the source/handoff validation boundary.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.authorization.membership import require_active_tenant_permission
from app.evaluation.contracts import EvaluationCase
from app.exams.service import ExamStudioService, exam_checksum
from app.models import (
    CandidateExamAttempt,
    CandidateResponseRevision,
    EvaluationDataset,
    EvaluationDatasetVersion,
    ExamVersion,
    QuestionBankItemVersion,
    SpeakingExecutionJob,
    SpeechTranscriptionJob,
    SpeechTranscriptionVersion,
    StandardPackVersion,
    SubjectiveAssessmentVersion,
    WritingExecutionJob,
)
from app.models.enums import TranscriptionStatus
from app.speaking_execution.execution import SpeakingExecutionService
from app.speech_transcription.execution import TranscriptionExecutionService
from app.standard_packs import StandardPackService
from app.writing_assessment.service import canonical_checksum
from app.writing_execution.execution import WritingExecutionService

BUNDLE_KEYS = (
    "model_profile_id",
    "model_version",
    "prompt_version_id",
    "agent_version_id",
    "workflow_version_id",
    "profile_version",
)
SOURCE_KEYS = (
    "standard_pack_version_id",
    "standard_pack_checksum",
    "rubric_version_id",
    "rubric_checksum",
    "question_bank_item_version_id",
    "item_checksum",
)


class PinnedReplayError(ValueError):
    """Safe, inspectable replay failure category."""


def _identity(expected: dict[str, str], source: dict[str, Any]) -> None:
    if not expected or any(
        key not in source or str(source[key]) != value for key, value in expected.items()
    ):
        raise PinnedReplayError("PINNED_EVIDENCE_IDENTITY_MISMATCH")


def _authorize(
    session: Session,
    context: TenantContext,
    version: EvaluationDatasetVersion,
    organisation_id: uuid.UUID,
    permission: str,
) -> None:
    dataset = session.get(EvaluationDataset, version.dataset_id)
    if dataset is None or dataset.organisation_id != organisation_id:
        raise PinnedReplayError("PINNED_EXECUTION_UNAVAILABLE")
    scoped = build_tenant_context(session, context.user_id, organisation_id)
    require_active_tenant_permission(session, scoped, permission, organisation_id)


def _source_pins(configuration: dict[str, Any], source: dict[str, Any]) -> None:
    if any(configuration.get(key) != source.get(key) for key in SOURCE_KEYS):
        raise PinnedReplayError("PINNED_EXECUTION_CONFIGURATION_MISMATCH")


def _assessment(
    session: Session,
    context: TenantContext,
    version: EvaluationDatasetVersion,
    case: dict[str, Any],
    configuration: dict[str, Any],
    job: WritingExecutionJob | SpeakingExecutionJob,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    _authorize(session, context, version, job.organisation_id, "subjective.review")
    if job.status != "COMPLETED" or job.completed_at is None:
        raise PinnedReplayError("COMPLETED_PINNED_EXECUTION_REQUIRED")
    assessment = session.get(SubjectiveAssessmentVersion, job.assessment_version_id)
    if assessment is None or assessment.ai_run_id != job.ai_run_id:
        raise PinnedReplayError("PINNED_EXECUTION_HANDOFF_MISMATCH")
    proposal = copy.deepcopy(job.validated_proposal)
    if isinstance(job, WritingExecutionJob):
        WritingExecutionService.validate_handoff(session, assessment, job.ai_run_id, proposal)
        _, _, model_input = WritingExecutionService.verify_source(session, job)
        provider_allowed = "OPENAI"
    else:
        SpeakingExecutionService.validate_handoff(session, assessment, job.ai_run_id, proposal)
        _, _, model_input = SpeakingExecutionService.verify_source(session, job)
        provider_allowed = "AZURE_OPENAI"
    snapshot = job.execution_snapshot
    if any(configuration.get(key) != snapshot.get(key) for key in BUNDLE_KEYS):
        raise PinnedReplayError("PINNED_EXECUTION_CONFIGURATION_MISMATCH")
    _source_pins(configuration, job.source_identity)
    source = {
        **job.source_identity,
        "response_revision_id": str(job.candidate_response_revision_id),
        "response_checksum": job.response_checksum,
    }
    _identity(case["input"]["evidence_identity"], source)
    _identity(case["reference"]["evidence_identity"], source)
    if isinstance(job, WritingExecutionJob):
        if case["input"].get("text") != model_input["candidate_text"]:
            raise PinnedReplayError("PINNED_WRITING_TEXT_MISMATCH")
        if case["input"].get("modality") != "TEXT":
            raise PinnedReplayError("PINNED_EXECUTION_MODALITY_MISMATCH")
        modality = "TEXT"
    else:
        if (
            case["input"].get("text") is not None
            or case["input"].get("modality") != "AUDIO"
            or case["input"].get("audio_sha256") != job.audio_sha256
            or case["input"].get("duration_ms") != job.verified_duration_ms
        ):
            raise PinnedReplayError("PINNED_ORIGINAL_AUDIO_MISMATCH")
        modality = "AUDIO"
    provider = snapshot.get("provider")
    request_id = job.provider_request_id
    test_only = provider == "DETERMINISTIC_TEST"
    if (
        provider not in {"DETERMINISTIC_TEST", provider_allowed}
        or not request_id
        or request_id.startswith("test-only-") is not test_only
        or job.output_checksum != canonical_checksum(proposal)
    ):
        raise PinnedReplayError("PINNED_PROVIDER_RECEIPT_MISMATCH")
    execution_class = "TEST_ONLY" if test_only else "REAL_PROVIDER"
    observation = {
        "modality": modality,
        "evidence_identity": copy.deepcopy(case["reference"]["evidence_identity"]),
        "criteria": [
            {
                "criterion_code": row["criterion_code"],
                "proposed_points": row["proposed_points"],
                "evidence": copy.deepcopy(row["evidence"]),
            }
            for row in proposal["criteria"]
        ],
    }
    if isinstance(job, SpeakingExecutionJob):
        observation["audio_sha256"] = job.audio_sha256
    receipt = {
        "execution_job_id": str(job.id),
        "ai_run_id": str(job.ai_run_id),
        "assessment_version_id": str(job.assessment_version_id),
        "provider": provider,
        "model_name": snapshot["model_name"],
        "adapter_version": snapshot["adapter_version"],
        "profile_version": job.profile_version,
        "snapshot_checksum": job.snapshot_checksum,
        "input_checksum": job.input_checksum,
        "output_checksum": job.output_checksum,
        "provider_request_id": request_id,
        "test_only": test_only,
        "execution_class": execution_class,
    }
    return case, observation, receipt, execution_class


def _transcription(
    session: Session,
    context: TenantContext,
    version: EvaluationDatasetVersion,
    case: dict[str, Any],
    configuration: dict[str, Any],
    job: SpeechTranscriptionJob,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    _authorize(session, context, version, job.organisation_id, "speech_transcription.read")
    if job.status != "READY" or job.completed_at is None:
        raise PinnedReplayError("COMPLETED_PINNED_EXECUTION_REQUIRED")
    TranscriptionExecutionService._verify_job_source(session, job)
    snapshot = job.profile_snapshot
    if (
        configuration.get("model_profile_id") != str(job.profile_id)
        or configuration.get("model_version") != job.profile_version
        or configuration.get("profile_version") != job.profile_version
        or any(
            configuration.get(key) is not None
            for key in ("prompt_version_id", "agent_version_id", "workflow_version_id")
        )
    ):
        raise PinnedReplayError("PINNED_EXECUTION_CONFIGURATION_MISMATCH")
    # ASR's original receipt predates pack pins. Derive the exact version via
    # its recorded ExamVersion FK, never by selecting a current/latest pack.
    attempt = session.get(CandidateExamAttempt, job.attempt_id)
    exam = session.get(ExamVersion, uuid.UUID(str(job.source_identity["exam_version_id"])))
    pack = session.get(StandardPackVersion, exam.standard_pack_version_id) if exam else None
    item = session.get(
        QuestionBankItemVersion, uuid.UUID(str(job.source_identity["item_version_id"]))
    )
    if (
        attempt is None
        or exam is None
        or pack is None
        or item is None
        or attempt.exam_version_id != exam.id
        or str(attempt.runtime_plan.get("exam_version_id")) != str(exam.id)
        or str(attempt.runtime_plan.get("standard_pack_version_id")) != str(pack.id)
        or canonical_checksum(attempt.runtime_plan) != attempt.runtime_plan_checksum
        or not exam.checksum
        or exam_checksum(ExamStudioService._payload(session, exam)) != exam.checksum
        or job.source_identity.get("exam_version_checksum", exam.checksum) != exam.checksum
        or not pack.configuration_checksum
        or StandardPackService.checksum(pack) != pack.configuration_checksum
        or item.checksum != job.source_identity["item_version_checksum"]
    ):
        raise PinnedReplayError("PINNED_TRANSCRIPTION_SOURCE_VERSION_MISMATCH")
    derived_source = {
        "standard_pack_version_id": str(pack.id),
        "standard_pack_checksum": pack.configuration_checksum,
        "question_bank_item_version_id": str(item.id),
        "item_checksum": item.checksum,
        "rubric_version_id": None,
        "rubric_checksum": None,
    }
    _source_pins(configuration, derived_source)
    source = {
        **job.source_identity,
        "response_revision_id": str(job.candidate_response_revision_id),
        "audio_sha256": job.audio_sha256,
    }
    revision = session.get(CandidateResponseRevision, job.candidate_response_revision_id)
    if revision is None or canonical_checksum(revision.payload) != revision.payload_checksum:
        raise PinnedReplayError("PINNED_TRANSCRIPTION_SOURCE_VERSION_MISMATCH")
    source["response_checksum"] = revision.payload_checksum
    _identity(case["input"]["evidence_identity"], source)
    _identity(case["reference"]["evidence_identity"], source)
    if (
        case["input"].get("text") is not None
        or case["input"].get("modality") != "AUDIO"
        or case["input"].get("audio_sha256") != job.audio_sha256
        or case["input"].get("duration_ms") != job.verified_duration_ms
        or case["input"].get("language") != job.language
    ):
        raise PinnedReplayError("PINNED_ORIGINAL_AUDIO_MISMATCH")
    transcript = session.scalar(
        select(SpeechTranscriptionVersion).where(
            SpeechTranscriptionVersion.execution_job_id == job.id
        )
    )
    if (
        transcript is None
        or transcript.status != TranscriptionStatus.READY
        or transcript.profile_id != job.profile_id
        or transcript.profile_version != job.profile_version
        or transcript.candidate_media_asset_id != job.candidate_media_asset_id
        or transcript.audio_sha256 != job.audio_sha256
        or transcript.configuration_checksum != job.configuration_checksum
        or transcript.provider != snapshot["provider"]
        or transcript.model_name != snapshot["model_name"]
        or transcript.adapter_version != snapshot["adapter_version"]
        or transcript.provenance.get("execution_id") != str(job.id)
        or transcript.provenance.get("source") != job.source_identity
        or transcript.provenance.get("snapshot_checksum") != job.snapshot_checksum
        or transcript.checksum
        != canonical_checksum(
            {
                "execution_id": str(job.id),
                "snapshot_checksum": job.snapshot_checksum,
                "transcript": transcript.transcript,
                "segments": transcript.segments,
            }
        )
    ):
        raise PinnedReplayError("PINNED_TRANSCRIPTION_VERSION_MISMATCH")
    test_only = snapshot["provider"] == "DETERMINISTIC_TEST"
    if not test_only:
        # Current ASR history has no persisted external request receipt. Do not
        # invent one or promote database serialization into genuine inference.
        raise PinnedReplayError("PINNED_TRANSCRIPTION_PROVIDER_RECEIPT_UNAVAILABLE")
    if transcript.provenance.get("test_only") is not True:
        raise PinnedReplayError("PINNED_PROVIDER_RECEIPT_MISMATCH")
    observation = {
        "text": transcript.transcript,
        "language": transcript.language,
        "audio_sha256": transcript.audio_sha256,
        "modality": "AUDIO",
        "evidence_identity": copy.deepcopy(case["reference"]["evidence_identity"]),
        "segments": [
            {key: row[key] for key in ("start_ms", "end_ms", "text")} for row in transcript.segments
        ],
    }
    receipt = {
        "execution_job_id": str(job.id),
        "transcription_version_id": str(transcript.id),
        "provider": snapshot["provider"],
        "model_name": snapshot["model_name"],
        "adapter_version": snapshot["adapter_version"],
        "profile_version": job.profile_version,
        "snapshot_checksum": job.snapshot_checksum,
        "input_checksum": canonical_checksum(job.source_identity),
        "output_checksum": transcript.checksum,
        "provider_request_id": None,
        "test_only": True,
        "execution_class": "TEST_ONLY",
        "pack_provenance": "DERIVED_VIA_PINNED_EXAM_VERSION",
    }
    return case, observation, receipt, "TEST_ONLY"


def load_pinned_observation(
    session: Session,
    context: TenantContext,
    dataset: EvaluationDatasetVersion,
    case: dict[str, Any],
    configuration: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    """Revalidate an exact historical execution, requiring private-evidence authority."""
    parsed = EvaluationCase.model_validate(case)
    if parsed.synthetic_observation is not None or parsed.execution_job_id is None:
        raise PinnedReplayError("EXACT_EXISTING_EXECUTION_REQUIRED")
    try:
        identifier = uuid.UUID(parsed.execution_job_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise PinnedReplayError("PINNED_EXECUTION_UNAVAILABLE") from exc
    private_case = parsed.model_dump(mode="json")
    try:
        if parsed.kind == "WRITING":
            writing = session.get(WritingExecutionJob, identifier)
            if writing is None:
                raise PinnedReplayError("PINNED_EXECUTION_UNAVAILABLE")
            return _assessment(session, context, dataset, private_case, configuration, writing)
        if parsed.kind == "SPEAKING":
            speaking = session.get(SpeakingExecutionJob, identifier)
            if speaking is None:
                raise PinnedReplayError("PINNED_EXECUTION_UNAVAILABLE")
            return _assessment(session, context, dataset, private_case, configuration, speaking)
        if parsed.kind == "TRANSCRIPTION":
            transcription = session.get(SpeechTranscriptionJob, identifier)
            if transcription is None:
                raise PinnedReplayError("PINNED_EXECUTION_UNAVAILABLE")
            return _transcription(
                session, context, dataset, private_case, configuration, transcription
            )
        raise PinnedReplayError("PINNED_RETRIEVAL_EXECUTION_UNAVAILABLE")
    except (AuthorizationError, PinnedReplayError):
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        # Existing source validators use safe failure categories. Do not pass
        # arbitrary database/provider/source exception details into summaries.
        raise PinnedReplayError("PINNED_EXECUTION_PROVENANCE_INVALID") from exc
