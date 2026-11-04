"""Durable, exact-version Speaking proposal execution and governed human handoff."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
import unicodedata
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai_orchestration.service import AIOrchestrationService
from app.audit import AuditService
from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.authorization.membership import require_active_tenant_permission
from app.config import Settings
from app.knowledge.storage import ObjectStorage, StorageError, StorageIntegrityError
from app.models import (
    AIRun,
    AIRunStep,
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponse,
    CandidateResponseMediaBinding,
    CandidateResponseRevision,
    ExamVersion,
    ExamVersionItemBinding,
    HumanReviewRequest,
    QuestionBankItemVersion,
    RubricVersion,
    SpeakingExecutionJob,
    StandardPackTaskSpecification,
    StandardPackVersion,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
)
from app.models.enums import (
    ActorType,
    AIRunStatus,
    CandidateAttemptStatus,
    PackResponseMode,
    QuestionBankSkill,
    RubricVersionStatus,
    SubjectiveAssessmentStatus,
)
from app.speaking_assessment.service import SpeakingAssessmentService
from app.speaking_audio.evidence import resolve_submitted_audio
from app.speaking_audio.service import SpeakingAudioService
from app.writing_assessment.service import (
    RubricService,
    SubjectiveAssessmentService,
    canonical_checksum,
)

ACTIVE_STATES = ("QUEUED", "RUNNING", "RETRYABLE")
RETRYABLE = {
    "PROVIDER_TIMEOUT",
    "PROVIDER_UNAVAILABLE",
    "DATABASE_UNAVAILABLE",
    "OBJECT_STORAGE_UNAVAILABLE",
    "WORKER_LEASE_EXPIRED",
}
SAFE_FAILURES = RETRYABLE | {
    "INVALID_PROVIDER_OUTPUT",
    "STALE_SPEAKING_EVIDENCE",
    "PROFILE_APPROVAL_INVALID",
    "REQUEST_AUTHORITY_REVOKED",
    "RETRY_EXHAUSTED",
    "CANCELLED",
    "UNCONFIGURED",
    "PROVIDER_UNCONFIGURED",
    "PROVIDER_POLICY_BLOCK",
    "AUDIO_TRANSPORT_UNSUPPORTED",
    "STALE_AUDIO_EVIDENCE",
}
SessionFactory = Callable[[], Session]
ProviderFactory = Callable[[dict[str, Any], Settings], Any]


class SpeakingExecutionError(ValueError):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def now() -> datetime:
    return datetime.now(timezone.utc)


def utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _text(value: Any, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")
    if any(unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in value):
        raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")
    return value


class SpeakingExecutionService:
    @staticmethod
    def authorize(
        session: Session,
        context: TenantContext,
        organisation_id: uuid.UUID,
        permission: str,
        *,
        candidate_user_id: uuid.UUID | None = None,
    ) -> TenantContext:
        if context.organisation_id != organisation_id or context.user_id == candidate_user_id:
            raise AuthorizationError("institution Speaking evidence authority required")
        trusted = build_tenant_context(session, context.user_id, organisation_id)
        require_active_tenant_permission(session, trusted, permission, organisation_id)
        return trusted

    @staticmethod
    def assert_audio_policy(rubric: RubricVersion) -> None:
        policy = rubric.ai_assessment_policy
        evidence = rubric.evidence_policy
        if (
            rubric.skill is not QuestionBankSkill.SPEAKING
            or policy.get("enabled") is not True
            or policy.get("mode") != "AI_ASSISTED"
            or policy.get("evidence_mode") != "AUDIO"
            or evidence.get("mode") != "AUDIO"
            or evidence.get("time_unit") != "milliseconds"
            or evidence.get("required") is not True
            or type(evidence.get("minimum_references")) is not int
            or not 1 <= evidence["minimum_references"] <= 10
            or not rubric.criteria
            or any(c.get("evidence_mode") != "AUDIO" for c in rubric.criteria)
            or rubric.human_review_policy.get("required") is not True
        ):
            raise SpeakingExecutionError("EXPLICIT_AI_ASSISTED_AUDIO_POLICY_REQUIRED")
        try:
            uuid.UUID(str(policy["execution_profile_id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise SpeakingExecutionError("SPEAKING_EXECUTION_POLICY_MISMATCH") from exc
        if re.fullmatch(r"[a-f0-9]{64}", str(policy.get("execution_profile_version", ""))) is None:
            raise SpeakingExecutionError("SPEAKING_EXECUTION_POLICY_MISMATCH")

    @classmethod
    def source(
        cls,
        session: Session,
        *,
        attempt_id: uuid.UUID,
        binding_id: uuid.UUID,
        rubric_version_id: uuid.UUID,
        response_revision_id: uuid.UUID | None = None,
        response_checksum: str | None = None,
        rubric_checksum: str | None = None,
        candidate_media_asset_id: uuid.UUID | None = None,
        audio_sha256: str | None = None,
        duration_ms: int | None = None,
        recording_policy_checksum: str | None = None,
        assessment_policy_checksum: str | None = None,
        lock: bool = False,
    ) -> tuple[CandidateExamAttempt, CandidateResponseRevision, RubricVersion, dict[str, Any]]:
        query = select(CandidateExamAttempt).where(CandidateExamAttempt.id == attempt_id)
        if lock:
            query = query.with_for_update()
        attempt = session.scalar(query.execution_options(populate_existing=True))
        binding = session.get(ExamVersionItemBinding, binding_id)
        rubric = session.get(RubricVersion, rubric_version_id)
        if (
            attempt is None
            or attempt.status is not CandidateAttemptStatus.SUBMITTED
            or binding is None
            or rubric is None
        ):
            raise SpeakingExecutionError("SPEAKING_SOURCE_NOT_ELIGIBLE")
        if binding.task.section.module.exam_version_id != attempt.exam_version_id:
            raise SpeakingExecutionError("SPEAKING_BINDING_MISMATCH")
        cls.assert_audio_policy(rubric)
        if (
            rubric.status not in {RubricVersionStatus.PUBLISHED, RubricVersionStatus.ACTIVE}
            or rubric.rubric.organisation_id != attempt.organisation_id
        ):
            raise SpeakingExecutionError("SPEAKING_RUBRIC_NOT_PUBLISHED")
        exam = session.get(ExamVersion, attempt.exam_version_id)
        item = session.get(QuestionBankItemVersion, binding.question_bank_item_version_id)
        task = session.get(StandardPackTaskSpecification, binding.task.standard_pack_task_spec_id)
        pack = session.get(StandardPackVersion, rubric.standard_pack_version_id)
        if (
            item is None
            or item.skill is not QuestionBankSkill.SPEAKING
            or item.response_mode is not PackResponseMode.AUDIO
            or task is None
            or exam is None
        ):
            raise SpeakingExecutionError("SPEAKING_SOURCE_NOT_ELIGIBLE")
        if rubric.task_spec_id != task.id or item.rubric_reference != str(rubric.id):
            raise SpeakingExecutionError("SPEAKING_RUBRIC_BINDING_MISMATCH")
        if (
            pack is None
            or not pack.configuration_checksum
            or exam.standard_pack_version_id != pack.id
            or str(attempt.runtime_plan.get("standard_pack_version_id")) != str(pack.id)
            or item.language != rubric.language
            or item.assessment_family != rubric.assessment_family
        ):
            raise SpeakingExecutionError("SPEAKING_PACK_MISMATCH")
        if canonical_checksum(RubricService.checksum_payload(rubric)) != rubric.checksum:
            raise SpeakingExecutionError("STALE_SPEAKING_RUBRIC")
        if canonical_checksum(attempt.runtime_plan) != attempt.runtime_plan_checksum:
            raise SpeakingExecutionError("STALE_SPEAKING_RUNTIME_PLAN")
        response = session.scalar(
            select(CandidateResponse)
            .where(
                CandidateResponse.attempt_id == attempt.id,
                CandidateResponse.exam_version_item_binding_id == binding.id,
            )
            .execution_options(populate_existing=True)
        )
        revision = (
            session.scalar(
                select(CandidateResponseRevision)
                .where(
                    CandidateResponseRevision.candidate_response_id == response.id,
                    CandidateResponseRevision.revision_number == response.current_revision,
                )
                .execution_options(populate_existing=True)
            )
            if response
            else None
        )
        media_binding = (
            session.scalar(
                select(CandidateResponseMediaBinding).where(
                    CandidateResponseMediaBinding.candidate_response_revision_id == revision.id,
                    CandidateResponseMediaBinding.binding_role == "PRIMARY_AUDIO",
                )
            )
            if revision
            else None
        )
        if (
            response is None
            or revision is None
            or media_binding is None
            or revision.payload_checksum != canonical_checksum(revision.payload)
        ):
            raise SpeakingExecutionError("STALE_SPEAKING_RESPONSE")
        if (
            candidate_media_asset_id is not None
            and candidate_media_asset_id != media_binding.candidate_media_asset_id
        ):
            raise SpeakingExecutionError("STALE_AUDIO_EVIDENCE")
        asset, audio_revision, bound, audio = resolve_submitted_audio(
            session,
            media_binding.candidate_media_asset_id,
            response_revision_id=response_revision_id,
            audio_sha256=audio_sha256,
            lock=lock,
        )
        if (
            asset.attempt_id != attempt.id
            or asset.exam_version_item_binding_id != binding.id
            or audio_revision.id != revision.id
        ):
            raise SpeakingExecutionError("STALE_AUDIO_EVIDENCE")
        if (
            response_checksum is not None
            and response_checksum != revision.payload_checksum
            or rubric_checksum is not None
            and rubric_checksum != rubric.checksum
            or duration_ms is not None
            and (type(duration_ms) is not int or duration_ms != asset.duration_ms)
            or recording_policy_checksum is not None
            and recording_policy_checksum != audio["policy_checksum"]
            or assessment_policy_checksum is not None
            and assessment_policy_checksum != canonical_checksum(rubric.ai_assessment_policy)
        ):
            raise SpeakingExecutionError("STALE_SPEAKING_EVIDENCE")
        if (
            any(b.required for b in rubric.evidence_bindings)
            or rubric.evidence_policy.get("source_backed") is True
        ):
            raise SpeakingExecutionError("SOURCE_BACKED_SPEAKING_UNCONFIGURED")
        source = {
            **audio,
            "exam_version_id": str(exam.id),
            "exam_version_checksum": exam.checksum,
            "runtime_plan_checksum": attempt.runtime_plan_checksum,
            "question_bank_item_version_id": str(item.id),
            "item_checksum": item.checksum,
            "item_content_checksum": canonical_checksum(item.content),
            "candidate_response_id": str(response.id),
            "candidate_response_revision_id": str(revision.id),
            "response_checksum": revision.payload_checksum,
            "standard_pack_version_id": str(pack.id),
            "standard_pack_checksum": pack.configuration_checksum,
            "rubric_version_id": str(rubric.id),
            "rubric_checksum": rubric.checksum,
            "assessment_mode": "AI_ASSISTED",
            "evidence_mode": "AUDIO",
            "recording_policy_checksum": audio["policy_checksum"],
            "assessment_policy_checksum": canonical_checksum(rubric.ai_assessment_policy),
            "speech_transcription_version_id": None,
            "transcript_checksum": None,
            "task_label": task.name,
        }
        return attempt, revision, rubric, source

    @staticmethod
    def build_input(
        job_id: uuid.UUID,
        run_id: uuid.UUID,
        revision: CandidateResponseRevision,
        rubric: RubricVersion,
        source: dict[str, Any],
        task_prompt: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "execution_id": str(job_id),
            "ai_run_id": str(run_id),
            "source": {
                "response_revision_id": str(revision.id),
                "response_checksum": revision.payload_checksum,
                "rubric_version_id": str(rubric.id),
                "rubric_checksum": rubric.checksum,
                "candidate_media_asset_id": source["candidate_media_asset_id"],
                "audio_sha256": source["audio_sha256"],
                "duration_ms": source["duration_ms"],
            },
            "task_prompt": task_prompt,
            "language": rubric.language,
            "cefr_levels": rubric.cefr_levels,
            "rubric": {
                "criteria": rubric.criteria,
                "performance_levels": rubric.performance_levels,
                "aggregation_policy": rubric.aggregation_policy,
                "evidence_policy": rubric.evidence_policy,
            },
            "approved_sources": [],
        }

    @classmethod
    def verify_source(
        cls, session: Session, job: SpeakingExecutionJob
    ) -> tuple[CandidateResponseRevision, RubricVersion, dict[str, Any]]:
        from app.speaking_execution.registry import snapshot_registry_valid

        try:
            _, revision, rubric, source = cls.source(
                session,
                attempt_id=job.attempt_id,
                binding_id=job.exam_version_item_binding_id,
                rubric_version_id=job.rubric_version_id,
                response_revision_id=job.candidate_response_revision_id,
                response_checksum=job.response_checksum,
                rubric_checksum=job.rubric_checksum,
                candidate_media_asset_id=job.candidate_media_asset_id,
                audio_sha256=job.audio_sha256,
                duration_ms=job.verified_duration_ms,
                recording_policy_checksum=job.recording_policy_checksum,
                assessment_policy_checksum=job.source_identity["assessment_policy_checksum"],
            )
        except ValueError as exc:
            raise SpeakingExecutionError("STALE_SPEAKING_EVIDENCE") from exc
        if (
            source != job.source_identity
            or canonical_checksum(job.execution_snapshot) != job.snapshot_checksum
        ):
            raise SpeakingExecutionError("STALE_SPEAKING_EVIDENCE")
        if not snapshot_registry_valid(session, job.execution_snapshot):
            raise SpeakingExecutionError("PROFILE_APPROVAL_INVALID")
        item = session.get(QuestionBankItemVersion, job.question_bank_item_version_id)
        assert item is not None
        model_input = cls.build_input(
            job.id, job.ai_run_id, revision, rubric, source, str(item.content.get("prompt", ""))
        )
        if canonical_checksum(model_input) != job.input_checksum:
            raise SpeakingExecutionError("STALE_SPEAKING_EVIDENCE")
        return revision, rubric, model_input

    @staticmethod
    def audit(session: Session, job: SpeakingExecutionJob, event: str) -> None:
        AuditService.record(
            session,
            TenantContext(
                user_id=job.requested_by_user_id,
                organisation_id=job.organisation_id,
                membership_id=job.requested_membership_id,
            ),
            event_type=event,
            entity_type="SPEAKING_EXECUTION_JOB",
            entity_id=str(job.id),
            organisation_id=job.organisation_id,
            actor_type=ActorType.SYSTEM_SERVICE,
            require_audit_permission=False,
            metadata={
                "job_id": str(job.id),
                "ai_run_id": str(job.ai_run_id),
                "status": job.status,
                "attempt_count": job.attempt_count,
                "failure_category": job.failure_category,
                "profile_version": job.profile_version,
            },
        )

    @staticmethod
    def transition(
        job: SpeakingExecutionJob,
        status: str,
        *,
        category: str | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        timestamp = timestamp or now()
        job.status, job.updated_at, job.failure_category = status, timestamp, category
        job.state_history = [
            *job.state_history,
            {
                "status": status,
                "at": timestamp.isoformat(),
                "attempt_count": job.attempt_count,
                "failure_category": category,
            },
        ]
        if status in {"COMPLETED", "FAILED", "CANCELLED"}:
            job.completed_at = timestamp

    @classmethod
    def request(
        cls,
        session: Session,
        context: TenantContext,
        *,
        attempt_id: uuid.UUID,
        binding_id: uuid.UUID,
        rubric_version_id: uuid.UUID,
        candidate_response_revision_id: uuid.UUID,
        response_checksum: str,
        rubric_checksum: str,
        candidate_media_asset_id: uuid.UUID,
        audio_sha256: str,
        duration_ms: int,
        recording_policy_checksum: str,
        assessment_policy_checksum: str,
        execution_profile_id: uuid.UUID,
        execution_profile_version: str,
        model_profile_id: uuid.UUID,
        prompt_version_id: uuid.UUID,
        agent_version_id: uuid.UUID,
        workflow_version_id: uuid.UUID,
        operation_id: uuid.UUID,
        settings: Settings,
    ) -> SpeakingExecutionJob:
        from app.speaking_execution.registry import (
            approved_profile_record,
            snapshot_execution_profile,
        )
        from app.speaking_execution.runtime import validate_execution_configuration

        if context.organisation_id is None:
            raise AuthorizationError("institution Speaking evidence authority required")
        cls.authorize(session, context, context.organisation_id, "subjective.assess")
        scoped_attempt = session.scalar(
            select(CandidateExamAttempt).where(
                CandidateExamAttempt.id == attempt_id,
                CandidateExamAttempt.organisation_id == context.organisation_id,
            )
        )
        if scoped_attempt is None or scoped_attempt.candidate_user_id == context.user_id:
            raise AuthorizationError("institution Speaking evidence authority required")
        attempt, revision, rubric, source = cls.source(
            session,
            attempt_id=attempt_id,
            binding_id=binding_id,
            rubric_version_id=rubric_version_id,
            response_revision_id=candidate_response_revision_id,
            response_checksum=response_checksum,
            rubric_checksum=rubric_checksum,
            candidate_media_asset_id=candidate_media_asset_id,
            audio_sha256=audio_sha256,
            duration_ms=duration_ms,
            recording_policy_checksum=recording_policy_checksum,
            assessment_policy_checksum=assessment_policy_checksum,
            lock=True,
        )
        context = cls.authorize(
            session,
            context,
            attempt.organisation_id,
            "subjective.assess",
            candidate_user_id=attempt.candidate_user_id,
        )
        existing = session.scalar(
            select(SpeakingExecutionJob).where(SpeakingExecutionJob.operation_id == operation_id)
        )
        if existing is not None:
            if (
                existing.requested_by_user_id != context.user_id
                or existing.source_identity != source
                or existing.profile_version != execution_profile_version
                or existing.execution_profile_id != execution_profile_id
                or any(
                    existing.execution_snapshot.get(key) != str(value)
                    for key, value in {
                        "model_profile_id": model_profile_id,
                        "prompt_version_id": prompt_version_id,
                        "agent_version_id": agent_version_id,
                        "workflow_version_id": workflow_version_id,
                    }.items()
                )
            ):
                raise SpeakingExecutionError("SPEAKING_OPERATION_CONFLICT")
            return existing
        if (
            execution_profile_id != workflow_version_id
            or rubric.ai_assessment_policy.get("execution_profile_id") != str(execution_profile_id)
            or rubric.ai_assessment_policy.get("execution_profile_version")
            != execution_profile_version
        ):
            raise SpeakingExecutionError("SPEAKING_EXECUTION_POLICY_MISMATCH")
        snapshot = snapshot_execution_profile(
            session,
            model_profile_id=model_profile_id,
            prompt_version_id=prompt_version_id,
            agent_version_id=agent_version_id,
            workflow_version_id=workflow_version_id,
        )
        approval = approved_profile_record(session, snapshot)
        if approval is None or snapshot.get("profile_version") != execution_profile_version:
            raise SpeakingExecutionError("PROFILE_APPROVAL_INVALID")
        snapshot["approval"] = {
            "id": str(approval.id),
            "profile_version": execution_profile_version,
            "approved_by": str(approval.actor_id),
        }
        availability = validate_execution_configuration(snapshot, settings)
        if availability.get("availability") not in {"CONFIGURED", "TEST_ONLY"}:
            raise SpeakingExecutionError(
                str(availability.get("unavailable_reason") or "UNCONFIGURED")
            )
        active = session.scalar(
            select(SpeakingExecutionJob.id).where(
                SpeakingExecutionJob.attempt_id == attempt_id,
                SpeakingExecutionJob.exam_version_item_binding_id == binding_id,
                SpeakingExecutionJob.status.in_(ACTIVE_STATES),
            )
        )
        assessment = session.scalar(
            select(SubjectiveAssessment).where(
                SubjectiveAssessment.attempt_id == attempt_id,
                SubjectiveAssessment.exam_version_item_binding_id == binding_id,
            )
        )
        current = SubjectiveAssessmentService.current_version(assessment) if assessment else None
        if (
            active
            or current is not None
            and current.status
            not in {
                SubjectiveAssessmentStatus.FAILED,
                SubjectiveAssessmentStatus.REJECTED,
                SubjectiveAssessmentStatus.CHANGES_REQUESTED,
                SubjectiveAssessmentStatus.VOIDED,
            }
        ):
            raise SpeakingExecutionError("SPEAKING_REVIEW_OR_EXECUTION_ALREADY_CURRENT")
        version = SubjectiveAssessmentService.create(
            session,
            context,
            attempt_id=attempt_id,
            binding_id=binding_id,
            rubric_version_id=rubric_version_id,
            _execution_origin=True,
            skill=QuestionBankSkill.SPEAKING,
            media_asset_id=candidate_media_asset_id,
        )
        job_id, run_id = uuid.uuid4(), uuid.uuid4()
        run = AIRun(
            id=run_id,
            workflow_version_id=workflow_version_id,
            agent_version_id=agent_version_id,
            status=AIRunStatus.PENDING,
            request_id=str(operation_id),
            organisation_id=attempt.organisation_id,
            standard_pack_version_id=rubric.standard_pack_version_id,
            standard_pack_checksum=source["standard_pack_checksum"],
            initiated_by_user_id=context.user_id,
        )
        session.add(run)
        session.flush()
        item = session.get(QuestionBankItemVersion, version.question_bank_item_version_id)
        assert item is not None
        model_input = cls.build_input(
            job_id, run_id, revision, rubric, source, str(item.content.get("prompt", ""))
        )
        if len(json.dumps(model_input, ensure_ascii=False).encode()) > 200_000:
            raise SpeakingExecutionError("SPEAKING_INPUT_TOO_LARGE")
        timestamp = now()
        assert context.membership_id is not None
        job = SpeakingExecutionJob(
            id=job_id,
            operation_id=operation_id,
            organisation_id=attempt.organisation_id,
            candidate_user_id=attempt.candidate_user_id,
            attempt_id=attempt.id,
            exam_version_id=attempt.exam_version_id,
            exam_version_item_binding_id=binding_id,
            question_bank_item_version_id=version.question_bank_item_version_id,
            candidate_response_id=revision.candidate_response_id,
            candidate_response_revision_id=revision.id,
            response_checksum=revision.payload_checksum,
            candidate_media_asset_id=candidate_media_asset_id,
            candidate_response_media_binding_id=uuid.UUID(
                source["candidate_response_media_binding_id"]
            ),
            recording_session_id=uuid.UUID(source["recording_session_id"]),
            audio_sha256=audio_sha256,
            verified_duration_ms=duration_ms,
            recording_policy_checksum=recording_policy_checksum,
            byte_size=source["byte_size"],
            content_type=source["content_type"],
            audio_transport={},
            standard_pack_version_id=rubric.standard_pack_version_id,
            standard_pack_checksum=source["standard_pack_checksum"],
            rubric_version_id=rubric.id,
            rubric_checksum=rubric.checksum,
            assessment_version_id=version.id,
            ai_run_id=run.id,
            requested_by_user_id=context.user_id,
            requested_membership_id=context.membership_id,
            execution_profile_id=execution_profile_id,
            profile_version=execution_profile_version,
            execution_snapshot=copy.deepcopy(snapshot),
            source_identity=source,
            snapshot_checksum=canonical_checksum(snapshot),
            input_checksum=canonical_checksum(model_input),
            validated_proposal={},
            status="QUEUED",
            attempt_count=0,
            max_attempts=3,
            next_attempt_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
            state_history=[],
        )
        cls.transition(job, "QUEUED", timestamp=timestamp)
        session.add(job)
        session.flush()
        cls.audit(session, job, "speaking_execution.requested")
        return job

    @classmethod
    def claim_next(
        cls,
        session: Session,
        *,
        worker_id: str,
        lease_seconds: int = 90,
        now: datetime | None = None,
    ) -> SpeakingExecutionJob | None:
        timestamp = now or globals()["now"]()
        if not worker_id or len(worker_id) > 120 or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid Speaking worker configuration")
        eligible = or_(
            and_(
                SpeakingExecutionJob.status.in_(("QUEUED", "RETRYABLE")),
                SpeakingExecutionJob.next_attempt_at <= timestamp,
            ),
            and_(
                SpeakingExecutionJob.status == "RUNNING",
                SpeakingExecutionJob.lease_expires_at <= timestamp,
            ),
        )
        while True:
            job = session.scalar(
                select(SpeakingExecutionJob)
                .where(eligible)
                .order_by(SpeakingExecutionJob.created_at, SpeakingExecutionJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
                .execution_options(populate_existing=True)
            )
            if job is None:
                return None
            run = session.get(AIRun, job.ai_run_id)
            version = session.get(SubjectiveAssessmentVersion, job.assessment_version_id)
            assert run is not None and version is not None
            if job.active_step_id:
                old = session.get(AIRunStep, job.active_step_id)
                if old and old.status == AIRunStatus.RUNNING:
                    old.status = AIRunStatus.FAILED
                    old.error_category = "WORKER_LEASE_EXPIRED"
                    old.completed_at = timestamp
            if job.attempt_count >= job.max_attempts:
                cls.transition(job, "FAILED", category="RETRY_EXHAUSTED", timestamp=timestamp)
                job.claim_token = None
                job.lease_expires_at = None
                run.status = AIRunStatus.FAILED
                run.completed_at = timestamp
                run.failure_category = "RETRY_EXHAUSTED"
                version.status = SubjectiveAssessmentStatus.FAILED
                cls.audit(session, job, "speaking_execution.failed")
                session.flush()
                continue
            job.attempt_count += 1
            job.claim_token = uuid.uuid4()
            job.claimed_by = worker_id
            job.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
            job.started_at = job.started_at or timestamp
            cls.transition(job, "RUNNING", timestamp=timestamp)
            run.status = AIRunStatus.RUNNING
            run.started_at = run.started_at or timestamp
            run.failure_category = None
            version.status = SubjectiveAssessmentStatus.AI_EVALUATION_RUNNING
            step = AIRunStep(
                run_id=run.id,
                sequence=job.attempt_count,
                node_id="speaking_proposal",
                agent_version_id=uuid.UUID(job.execution_snapshot["agent_version_id"]),
                model_profile_id=uuid.UUID(job.execution_snapshot["model_profile_id"]),
                prompt_version_id=uuid.UUID(job.execution_snapshot["prompt_version_id"]),
                input_checksum=job.input_checksum,
                status=AIRunStatus.RUNNING,
                token_usage={},
                retrieval_ids=[],
                rule_evaluation_ids=[],
                started_at=timestamp,
            )
            session.add(step)
            session.flush()
            job.active_step_id = step.id
            cls.audit(session, job, "speaking_execution.started")
            session.flush()
            return job

    @staticmethod
    def fenced(
        session: Session, job_id: uuid.UUID, claim_token: uuid.UUID
    ) -> SpeakingExecutionJob | None:
        job = session.scalar(
            select(SpeakingExecutionJob)
            .where(SpeakingExecutionJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (
            job
            if job is not None
            and job.status == "RUNNING"
            and job.claim_token == claim_token
            and job.lease_expires_at is not None
            and utc(job.lease_expires_at) > now()
            else None
        )

    @classmethod
    def heartbeat(
        cls, session: Session, *, job_id: uuid.UUID, claim_token: uuid.UUID, lease_seconds: int = 90
    ) -> bool:
        job = cls.fenced(session, job_id, claim_token)
        if job is None:
            return False
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid lease duration")
        job.lease_expires_at = now() + timedelta(seconds=lease_seconds)
        job.updated_at = now()
        session.flush()
        return True

    @staticmethod
    def validate_proposal(proposal: Any, model_input: dict[str, Any]) -> dict[str, Any]:
        expected = {
            "schema_version",
            "execution_id",
            "ai_run_id",
            "evidence_mode",
            "response_revision_id",
            "response_checksum",
            "rubric_version_id",
            "rubric_checksum",
            "candidate_media_asset_id",
            "audio_sha256",
            "duration_ms",
            "criteria",
            "consistency",
        }
        source = model_input["source"]
        if (
            not isinstance(proposal, dict)
            or set(proposal) != expected
            or proposal.get("schema_version") != "1"
            or proposal.get("evidence_mode") != "AUDIO"
            or proposal.get("execution_id") != model_input["execution_id"]
            or proposal.get("ai_run_id") != model_input["ai_run_id"]
            or any(proposal.get(key) != value for key, value in source.items())
            or type(proposal.get("duration_ms")) is not int
        ):
            raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")
        rows = proposal["criteria"]
        criteria = {c["code"]: c for c in model_input["rubric"]["criteria"]}
        levels = {v["code"] for v in model_input["rubric"]["performance_levels"]}
        if (
            not isinstance(rows, list)
            or len(rows) != len(criteria)
            or len(rows) > 50
            or any(not isinstance(c, dict) for c in rows)
            or {c.get("criterion_code") for c in rows} != set(criteria)
        ):
            raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")
        normalized = []
        for row in rows:
            if set(row) != {
                "criterion_code",
                "proposed_points",
                "proposed_level",
                "confidence",
                "evidence",
                "rationale",
                "uncertainties",
                "disagreement",
            }:
                raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")
            code = row["criterion_code"]
            raw = row["proposed_points"]
            if (
                not isinstance(raw, str)
                or re.fullmatch(r"(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,4})?", raw) is None
            ):
                raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")
            points = Decimal(raw)
            if (
                not points.is_finite()
                or not 0 <= points <= Decimal(str(criteria[code]["max_points"]))
                or row["proposed_level"] not in levels
                or row["confidence"] not in {"LOW", "MEDIUM", "HIGH"}
            ):
                raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")
            rationale = _text(row["rationale"], 2000)
            if (
                not isinstance(row["uncertainties"], list)
                or len(row["uncertainties"]) > 10
                or any(not isinstance(v, str) or len(v) > 240 for v in row["uncertainties"])
                or row["disagreement"] != {}
            ):
                raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")
            evidence = row["evidence"]
            minimum = model_input["rubric"]["evidence_policy"].get("minimum_references", 1)
            if not isinstance(evidence, list) or not minimum <= len(evidence) <= 10:
                raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")
            for ref in evidence:
                if (
                    not isinstance(ref, dict)
                    or set(ref)
                    != {"type", "candidate_media_asset_id", "audio_sha256", "start_ms", "end_ms"}
                    or ref["type"] != "AUDIO"
                    or ref["candidate_media_asset_id"] != source["candidate_media_asset_id"]
                    or ref["audio_sha256"] != source["audio_sha256"]
                    or type(ref["start_ms"]) is not int
                    or type(ref["end_ms"]) is not int
                    or not 0 <= ref["start_ms"] < ref["end_ms"] <= source["duration_ms"]
                ):
                    raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")
            SpeakingAssessmentService._reject_protected_inference(row)
            normalized.append(
                {
                    **copy.deepcopy(row),
                    "proposed_points": format(points, ".4f"),
                    "rationale": rationale.strip(),
                }
            )
        consistency = proposal["consistency"]
        if (
            not isinstance(consistency, dict)
            or set(consistency) != {"status", "criterion_codes"}
            or consistency["status"] not in {"PASS", "WARNING"}
            or not isinstance(consistency["criterion_codes"], list)
            or len(consistency["criterion_codes"]) > 50
            or any(code not in criteria for code in consistency["criterion_codes"])
        ):
            raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")
        return {
            **copy.deepcopy(proposal),
            "criteria": sorted(normalized, key=lambda r: r["criterion_code"]),
        }

    @staticmethod
    def validate_provider_receipt(
        result: Any, model_input: dict[str, Any], snapshot: dict[str, Any]
    ) -> None:
        """One strict boundary shared by worker and adapter contract acceptance."""
        from app.speaking_execution.runtime import MODEL, MODEL_REVISION, deployment_fingerprint

        metadata = result.metadata
        required = {
            "execution_id",
            "input_checksum",
            "output_schema_version",
            "test_only",
            "audio_sha256",
            "transport_sha256",
            "provider_model_revision",
        }
        genuine = snapshot["provider"] == "AZURE_OPENAI"
        if genuine:
            required |= {"deployment_fingerprint", "provider_response_model"}
        source = model_input["source"]
        if (
            snapshot["provider"] not in {"AZURE_OPENAI", "DETERMINISTIC_TEST"}
            or (result.provider, result.model_name, result.adapter_version)
            != (snapshot["provider"], snapshot["model_name"], snapshot["adapter_version"])
            or not isinstance(metadata, dict)
            or set(metadata) != required
            or metadata.get("execution_id") != model_input["execution_id"]
            or metadata.get("input_checksum") != canonical_checksum(model_input)
            or metadata.get("output_schema_version") != "1"
            or metadata.get("test_only") is not (not genuine)
            or metadata.get("audio_sha256") != source["audio_sha256"]
            or metadata.get("transport_sha256") != model_input["audio_transport"]["sha256"]
            or metadata.get("provider_model_revision")
            != snapshot["configuration"]["model_revision"]
            or (
                genuine
                and (
                    snapshot["model_name"] != MODEL
                    or snapshot["configuration"]["model_revision"] != MODEL_REVISION
                    or metadata.get("provider_response_model")
                    not in {MODEL, MODEL + "-" + MODEL_REVISION}
                    or metadata.get("deployment_fingerprint")
                    != deployment_fingerprint(snapshot["configuration"])
                )
            )
            or (
                not genuine
                and (
                    snapshot["model_name"] != "speaking-fixture-v1"
                    or snapshot["configuration"]["model_revision"] != "1"
                )
            )
            or not isinstance(result.provider_request_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,160}", result.provider_request_id) is None
        ):
            raise SpeakingExecutionError("INVALID_PROVIDER_OUTPUT")

    @classmethod
    def fail_claim(
        cls, session: Session, *, job_id: uuid.UUID, claim_token: uuid.UUID, category: str
    ) -> SpeakingExecutionJob | None:
        job = cls.fenced(session, job_id, claim_token)
        if job is None:
            return None
        category = category if category in SAFE_FAILURES else "INVALID_PROVIDER_OUTPUT"
        retryable = category in RETRYABLE and job.attempt_count < job.max_attempts
        cls.transition(job, "RETRYABLE" if retryable else "FAILED", category=category)
        job.claim_token = None
        job.lease_expires_at = None
        job.next_attempt_at = now() + timedelta(seconds=min(30, 2**job.attempt_count))
        run = session.get(AIRun, job.ai_run_id)
        step = session.get(AIRunStep, job.active_step_id) if job.active_step_id else None
        version = session.get(SubjectiveAssessmentVersion, job.assessment_version_id)
        assert run is not None and version is not None
        run.status = AIRunStatus.PENDING if retryable else AIRunStatus.FAILED
        run.failure_category = category
        if not retryable:
            run.completed_at = now()
            version.status = SubjectiveAssessmentStatus.FAILED
        if step:
            step.status = AIRunStatus.FAILED
            step.error_category = category
            step.completed_at = now()
        cls.audit(session, job, "speaking_execution.failed")
        session.flush()
        return job

    @classmethod
    def _execute_claim(
        cls,
        factory: SessionFactory,
        *,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        settings: Settings,
        storage: ObjectStorage,
        provider_factory: ProviderFactory | None = None,
    ) -> SpeakingExecutionJob | None:
        from app.speaking_execution.audio_transport import prepare_audio
        from app.speaking_execution.provider import InvalidProviderOutput
        from app.speaking_execution.runtime import (
            build_speaking_provider,
            validate_execution_configuration,
        )

        external_attempted = False
        try:
            with factory() as session:
                job = cls.fenced(session, job_id, claim_token)
                if job is None:
                    return None
                _, _, base_input = cls.verify_source(session, job)
                cls.authorize(
                    session,
                    build_tenant_context(session, job.requested_by_user_id, job.organisation_id),
                    job.organisation_id,
                    "subjective.assess",
                    candidate_user_id=job.candidate_user_id,
                )
                snapshot = copy.deepcopy(job.execution_snapshot)
                if validate_execution_configuration(snapshot, settings).get("availability") not in {
                    "CONFIGURED",
                    "TEST_ONLY",
                }:
                    raise SpeakingExecutionError("UNCONFIGURED")
                asset = session.get(CandidateMediaAsset, job.candidate_media_asset_id)
                assert asset is not None
                key, size, digest = asset.storage_key, job.byte_size, job.audio_sha256
                source = {
                    **copy.deepcopy(job.source_identity),
                    "validation_provenance": copy.deepcopy(asset.validation_provenance),
                }
                session.commit()
            try:
                backend = SpeakingAudioService.durable_storage(storage)
                original = backend.get_bounded(key, max_bytes=25_000_000, expected_size=size)
            except StorageIntegrityError as exc:
                raise SpeakingExecutionError("STALE_AUDIO_EVIDENCE") from exc
            except StorageError as exc:
                raise SpeakingExecutionError("OBJECT_STORAGE_UNAVAILABLE") from exc
            if len(original) != size or hashlib.sha256(original).hexdigest() != digest:
                raise SpeakingExecutionError("STALE_AUDIO_EVIDENCE")
            transport = prepare_audio(original, source=source, settings=settings)
            if (
                not isinstance(transport.content, bytes)
                or not transport.content
                or not isinstance(transport.metadata, dict)
                or transport.metadata.get("source_sha256") != digest
                or transport.metadata.get("sha256") != hashlib.sha256(transport.content).hexdigest()
                or transport.metadata.get("byte_size") != len(transport.content)
                or transport.metadata.get("format") != "wav"
                or transport.metadata.get("transform_version") != "PCM16_PRESERVE_TIMELINE_V1"
            ):
                raise SpeakingExecutionError("AUDIO_TRANSPORT_UNSUPPORTED")
            model_input = {**base_input, "audio_transport": copy.deepcopy(transport.metadata)}
            provider_checksum = canonical_checksum(model_input)
            provider = (provider_factory or build_speaking_provider)(snapshot, settings)
            if (provider.provider, provider.model_name, provider.adapter_version) != (
                snapshot["provider"],
                snapshot["model_name"],
                snapshot["adapter_version"],
            ):
                raise InvalidProviderOutput("PROVIDER_IDENTITY_MISMATCH")
            with factory() as session:
                job = cls.fenced(session, job_id, claim_token)
                if job is None:
                    return None
                cls.verify_source(session, job)
                cls.authorize(
                    session,
                    build_tenant_context(session, job.requested_by_user_id, job.organisation_id),
                    job.organisation_id,
                    "subjective.assess",
                    candidate_user_id=job.candidate_user_id,
                )
                if job.provider_input_checksum and (
                    job.provider_input_checksum != provider_checksum
                    or job.audio_transport != transport.metadata
                ):
                    raise SpeakingExecutionError("STALE_AUDIO_EVIDENCE")
                job.audio_transport = copy.deepcopy(transport.metadata)
                job.provider_input_checksum = provider_checksum
                step = session.get(AIRunStep, job.active_step_id)
                assert step is not None
                step.input_checksum = provider_checksum
                cls.audit(session, job, "speaking_execution.provider_requested")
                if snapshot["provider"] != "DETERMINISTIC_TEST":
                    cls.audit(session, job, "speaking_execution.external_processing_attempted")
                session.commit()
            external_attempted = snapshot["provider"] != "DETERMINISTIC_TEST"
            result = provider.generate(
                model_input,
                audio=transport.content,
                configuration=copy.deepcopy(snapshot["configuration"]),
                limits={
                    "max_input_bytes": 200000,
                    "max_audio_bytes": 25_000_000,
                    "max_output_bytes": 250000,
                },
            )
            cls.validate_provider_receipt(result, model_input, snapshot)
            genuine = snapshot["provider"] != "DETERMINISTIC_TEST"
            proposal = cls.validate_proposal(result.proposal, model_input)
            # The original remains the authoritative private evidence even if a
            # provider transport was prepared. Detect out-of-band replacement.
            try:
                current = backend.get_bounded(key, max_bytes=25_000_000, expected_size=size)
            except StorageIntegrityError as exc:
                raise SpeakingExecutionError("STALE_AUDIO_EVIDENCE") from exc
            except StorageError as exc:
                raise SpeakingExecutionError("OBJECT_STORAGE_UNAVAILABLE") from exc
            if hashlib.sha256(current).hexdigest() != digest:
                raise SpeakingExecutionError("STALE_AUDIO_EVIDENCE")
            with factory() as session:
                job = cls.fenced(session, job_id, claim_token)
                if job is None:
                    return None
                cls.verify_source(session, job)
                context = cls.authorize(
                    session,
                    build_tenant_context(session, job.requested_by_user_id, job.organisation_id),
                    job.organisation_id,
                    "subjective.assess",
                    candidate_user_id=job.candidate_user_id,
                )
                if validate_execution_configuration(job.execution_snapshot, settings).get(
                    "availability"
                ) not in {"CONFIGURED", "TEST_ONLY"}:
                    raise SpeakingExecutionError("UNCONFIGURED")
                if (
                    job.provider_input_checksum != provider_checksum
                    or job.audio_transport != transport.metadata
                ):
                    raise SpeakingExecutionError("STALE_AUDIO_EVIDENCE")
                step = session.get(AIRunStep, job.active_step_id)
                run = session.get(AIRun, job.ai_run_id)
                assert step is not None and run is not None
                job.validated_proposal = proposal
                job.output_checksum = canonical_checksum(proposal)
                job.provider_request_id = result.provider_request_id
                step.status = AIRunStatus.SUCCEEDED
                step.output_checksum = job.output_checksum
                step.provider_request_id = result.provider_request_id
                step.completed_at = now()
                step.latency_ms = (
                    max(0, int((now() - utc(step.started_at)).total_seconds() * 1000))
                    if step.started_at
                    else None
                )
                session.flush()
                AIOrchestrationService.complete_run(
                    session, run, context=context, execution_job_id=job.id, claim_token=claim_token
                )
                cls.transition(job, "COMPLETED")
                session.flush()
                SpeakingAssessmentService.propose(
                    session,
                    context,
                    job.assessment_version_id,
                    lambda _attempt: proposal,
                    ai_run_id=run.id,
                )
                if genuine:
                    cls.audit(session, job, "speaking_execution.external_processing_completed")
                cls.audit(session, job, "speaking_execution.completed")
                session.commit()
                return job
        except Exception as exc:
            category = getattr(exc, "category", None)
            if isinstance(exc, AuthorizationError):
                category = "REQUEST_AUTHORITY_REVOKED"
            if isinstance(exc, SQLAlchemyError):
                category = "DATABASE_UNAVAILABLE"
            if category not in SAFE_FAILURES:
                category = "INVALID_PROVIDER_OUTPUT"
            with factory() as session:
                job = cls.fail_claim(
                    session, job_id=job_id, claim_token=claim_token, category=category
                )
                if job is not None and external_attempted:
                    cls.audit(session, job, "speaking_execution.external_processing_failed")
                session.commit()
                return job

    @classmethod
    def execute_claim(
        cls,
        session_factory: SessionFactory,
        *,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        settings: Settings,
        provider_factory: ProviderFactory | None = None,
        storage: ObjectStorage | None = None,
    ) -> SpeakingExecutionJob | None:
        from app.knowledge.storage import build_object_storage

        backend = storage if storage is not None else build_object_storage(settings)
        with session_factory() as session:
            job = session.get(SpeakingExecutionJob, job_id)
            if job is None or job.lease_expires_at is None:
                return None
            seconds = max(
                1, min(3600, math.ceil((utc(job.lease_expires_at) - now()).total_seconds()))
            )
        stop = threading.Event()

        def renew() -> None:
            while not stop.wait(max(0.2, seconds / 3)):
                try:
                    with session_factory() as session:
                        valid = cls.heartbeat(
                            session, job_id=job_id, claim_token=claim_token, lease_seconds=seconds
                        )
                        session.commit()
                    if not valid:
                        return
                except Exception:
                    pass  # noqa: BLE001

        thread = threading.Thread(target=renew, daemon=True, name="speaking-lease")
        thread.start()
        try:
            return cls._execute_claim(
                session_factory,
                job_id=job_id,
                claim_token=claim_token,
                settings=settings,
                provider_factory=provider_factory,
                storage=backend,
            )
        finally:
            stop.set()
            thread.join(timeout=1)

    @staticmethod
    def can_retry(job: SpeakingExecutionJob) -> bool:
        return job.status == "RETRYABLE" and job.attempt_count < job.max_attempts

    @classmethod
    def retry(
        cls, session: Session, *, job: SpeakingExecutionJob, context: TenantContext
    ) -> SpeakingExecutionJob:
        cls.authorize(
            session,
            context,
            job.organisation_id,
            "subjective.assess",
            candidate_user_id=job.candidate_user_id,
        )
        if not cls.can_retry(job):
            raise SpeakingExecutionError("SPEAKING_RETRY_NOT_ALLOWED")
        cls.verify_source(session, job)
        job.next_attempt_at = now()
        cls.transition(job, "RETRYABLE", category=job.failure_category)
        cls.audit(session, job, "speaking_execution.retry_requested")
        return job

    @classmethod
    def cancel(
        cls, session: Session, *, job: SpeakingExecutionJob, context: TenantContext
    ) -> SpeakingExecutionJob:
        cls.authorize(
            session,
            context,
            job.organisation_id,
            "subjective.assess",
            candidate_user_id=job.candidate_user_id,
        )
        if job.status == "CANCELLED":
            return job
        if job.status not in ACTIVE_STATES:
            raise SpeakingExecutionError("SPEAKING_CANCEL_NOT_ALLOWED")
        cls.transition(job, "CANCELLED", category="CANCELLED")
        job.claim_token = None
        job.lease_expires_at = None
        run = session.get(AIRun, job.ai_run_id)
        version = session.get(SubjectiveAssessmentVersion, job.assessment_version_id)
        assert run is not None and version is not None
        run.status = AIRunStatus.CANCELLED
        run.completed_at = now()
        version.status = SubjectiveAssessmentStatus.FAILED
        step = session.get(AIRunStep, job.active_step_id) if job.active_step_id else None
        if step and step.status == AIRunStatus.RUNNING:
            step.status = AIRunStatus.CANCELLED
            step.completed_at = now()
        cls.audit(session, job, "speaking_execution.cancelled")
        session.flush()
        return job

    @staticmethod
    def job_payload(
        session: Session, job: SpeakingExecutionJob, context: TenantContext
    ) -> dict[str, Any]:
        version = session.get(SubjectiveAssessmentVersion, job.assessment_version_id)
        review = session.scalar(
            select(HumanReviewRequest).where(
                HumanReviewRequest.entity_type == "SUBJECTIVE_ASSESSMENT_VERSION",
                HumanReviewRequest.entity_id == job.assessment_version_id,
            )
        )
        snapshot = job.execution_snapshot
        run = session.get(AIRun, job.ai_run_id)
        allowed = False
        try:
            SpeakingExecutionService.authorize(
                session,
                context,
                job.organisation_id,
                "subjective.assess",
                candidate_user_id=job.candidate_user_id,
            )
            allowed = True
        except AuthorizationError:
            pass
        return {
            "id": str(job.id),
            "operation_id": str(job.operation_id),
            "organisation_id": str(job.organisation_id),
            "status": job.status,
            **{
                k: v
                for k, v in job.source_identity.items()
                if k not in {"candidate_user_id", "organisation_id"}
            },
            **{
                k: snapshot.get(k)
                for k in [
                    "model_profile_id",
                    "model_version",
                    "prompt_version_id",
                    "prompt_checksum",
                    "agent_version_id",
                    "agent_checksum",
                    "workflow_version_id",
                    "workflow_checksum",
                    "provider",
                    "model_name",
                    "adapter_version",
                ]
            },
            "execution_profile_id": str(job.execution_profile_id),
            "execution_profile_version": job.profile_version,
            "test_only": snapshot["provider"] == "DETERMINISTIC_TEST",
            "ai_run_id": str(job.ai_run_id),
            "ai_run_status": run.status.value if run else None,
            "assessment_id": str(version.subjective_assessment_id) if version else None,
            "assessment_version_id": str(job.assessment_version_id),
            "review_id": str(review.id) if review else None,
            "review_status": review.status.value if review else None,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "updated_at": job.updated_at.isoformat(),
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "failure_category": job.failure_category,
            "state_history": job.state_history,
            "can_retry": allowed and SpeakingExecutionService.can_retry(job),
            "can_cancel": allowed and job.status in ACTIVE_STATES,
            "assessment_mode": "AI_ASSISTED",
            "evidence_mode": "AUDIO",
        }

    @classmethod
    def validate_handoff(
        cls,
        session: Session,
        version: SubjectiveAssessmentVersion,
        run_id: uuid.UUID,
        proposal: dict[str, Any],
    ) -> SpeakingExecutionJob:
        job = session.scalar(
            select(SpeakingExecutionJob).where(
                SpeakingExecutionJob.ai_run_id == run_id,
                SpeakingExecutionJob.assessment_version_id == version.id,
            )
        )
        run = session.get(AIRun, run_id)
        if (
            job is None
            or job.status != "COMPLETED"
            or run is None
            or run.status != AIRunStatus.SUCCEEDED
            or run.completed_at is None
            or run.organisation_id != job.organisation_id
            or run.standard_pack_version_id != job.standard_pack_version_id
            or run.standard_pack_checksum != job.standard_pack_checksum
            or run.request_id != str(job.operation_id)
        ):
            raise SpeakingExecutionError("COMPLETED_SPEAKING_EXECUTION_REQUIRED")
        step = session.get(AIRunStep, job.active_step_id) if job.active_step_id else None
        snapshot = job.execution_snapshot
        if (
            step is None
            or step.run_id != run.id
            or step.status != AIRunStatus.SUCCEEDED
            or step.completed_at is None
            or not job.provider_input_checksum
            or step.input_checksum != job.provider_input_checksum
            or step.output_checksum != job.output_checksum
            or step.provider_request_id != job.provider_request_id
            or str(step.model_profile_id) != snapshot["model_profile_id"]
            or str(step.prompt_version_id) != snapshot["prompt_version_id"]
            or str(step.agent_version_id) != snapshot["agent_version_id"]
            or str(run.workflow_version_id) != snapshot["workflow_version_id"]
            or str(run.agent_version_id) != snapshot["agent_version_id"]
        ):
            raise SpeakingExecutionError("COMPLETED_SPEAKING_EXECUTION_REQUIRED")
        _, _, base_input = cls.verify_source(session, job)
        model_input = {**base_input, "audio_transport": job.audio_transport}
        if (
            not job.audio_transport
            or canonical_checksum(model_input) != job.provider_input_checksum
        ):
            raise SpeakingExecutionError("SPEAKING_EXECUTION_INPUT_MISMATCH")
        if (
            job.output_checksum != canonical_checksum(proposal)
            or job.validated_proposal != proposal
            or canonical_checksum(cls.validate_proposal(proposal, model_input))
            != job.output_checksum
        ):
            raise SpeakingExecutionError("SPEAKING_EXECUTION_OUTPUT_MISMATCH")
        if any(
            getattr(version, key) != getattr(job, key)
            for key in [
                "attempt_id",
                "exam_version_id",
                "exam_version_item_binding_id",
                "question_bank_item_version_id",
                "candidate_response_id",
                "candidate_response_revision_id",
                "response_checksum",
                "rubric_version_id",
                "rubric_checksum",
                "standard_pack_version_id",
                "candidate_media_asset_id",
                "audio_sha256",
            ]
        ):
            raise SpeakingExecutionError("SPEAKING_EXECUTION_SOURCE_MISMATCH")
        return job

    @staticmethod
    def execution_provenance(
        session: Session, version: SubjectiveAssessmentVersion
    ) -> dict[str, Any] | None:
        job = (
            session.scalar(
                select(SpeakingExecutionJob).where(
                    SpeakingExecutionJob.ai_run_id == version.ai_run_id
                )
            )
            if version.ai_run_id
            else session.scalar(
                select(SpeakingExecutionJob).where(
                    SpeakingExecutionJob.assessment_version_id == version.id
                )
            )
        )
        if job is None:
            return None
        run = session.get(AIRun, job.ai_run_id)
        snapshot = job.execution_snapshot
        return {
            "execution_job_id": str(job.id),
            "execution_status": job.status,
            "ai_run_id": str(job.ai_run_id),
            "ai_run_status": run.status.value if run else None,
            "execution_profile_id": str(job.execution_profile_id),
            "profile_version": job.profile_version,
            "audio_transport": job.audio_transport,
            "provider_input_checksum": job.provider_input_checksum,
            "evidence_mode": "AUDIO",
            **{
                key: snapshot.get(key)
                for key in [
                    "model_profile_id",
                    "model_version",
                    "prompt_version_id",
                    "prompt_checksum",
                    "agent_version_id",
                    "agent_checksum",
                    "workflow_version_id",
                    "workflow_checksum",
                    "provider",
                    "model_name",
                    "adapter_version",
                ]
            },
            **{
                k: v
                for k, v in job.source_identity.items()
                if k not in {"candidate_user_id", "organisation_id", "recording_policy"}
            },
            "candidate_response_revision_id": str(job.candidate_response_revision_id),
            "response_checksum": job.response_checksum,
            "rubric_version_id": str(job.rubric_version_id),
            "rubric_checksum": job.rubric_checksum,
            "test_only": snapshot["provider"] == "DETERMINISTIC_TEST",
        }
