"""Durable, exact-version Writing proposal execution and governed human handoff."""

from __future__ import annotations

import copy
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

from app import pilot
from app.ai_orchestration.service import AIOrchestrationService
from app.audit import AuditService
from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.authorization.membership import require_active_tenant_permission
from app.config import Settings
from app.models import (
    AIRun,
    AIRunStep,
    CandidateExamAttempt,
    CandidateResponse,
    CandidateResponseRevision,
    ExamVersion,
    ExamVersionItemBinding,
    HumanReviewRequest,
    QuestionBankItemVersion,
    RubricVersion,
    StandardPackTaskSpecification,
    StandardPackVersion,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
    WritingExecutionJob,
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
    "WORKER_LEASE_EXPIRED",
}
SAFE_FAILURES = RETRYABLE | {
    "INVALID_PROVIDER_OUTPUT",
    "STALE_WRITING_EVIDENCE",
    "PROFILE_APPROVAL_INVALID",
    "REQUEST_AUTHORITY_REVOKED",
    "RETRY_EXHAUSTED",
    "CANCELLED",
    "UNCONFIGURED",
    "PROVIDER_UNCONFIGURED",
    "PROVIDER_POLICY_BLOCK",
}
SessionFactory = Callable[[], Session]
ProviderFactory = Callable[[dict[str, Any], Settings], Any]


class WritingExecutionError(ValueError):
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
        raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
    if any(unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in value):
        raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
    return value


class WritingExecutionService:
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
            raise AuthorizationError("institution Writing evidence authority required")
        trusted = build_tenant_context(session, context.user_id, organisation_id)
        require_active_tenant_permission(session, trusted, permission, organisation_id)
        return trusted

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
        lock: bool = False,
    ) -> tuple[CandidateExamAttempt, CandidateResponseRevision, RubricVersion, dict[str, Any]]:
        statement = (
            select(CandidateExamAttempt)
            .where(CandidateExamAttempt.id == attempt_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        attempt = session.scalar(statement)
        binding = session.get(ExamVersionItemBinding, binding_id)
        rubric = session.get(RubricVersion, rubric_version_id)
        if (
            attempt is None
            or attempt.status != CandidateAttemptStatus.SUBMITTED
            or binding is None
            or rubric is None
        ):
            raise WritingExecutionError("WRITING_SOURCE_NOT_ELIGIBLE")
        if binding.task.section.module.exam_version_id != attempt.exam_version_id:
            raise WritingExecutionError("WRITING_BINDING_MISMATCH")
        exam = session.get(ExamVersion, attempt.exam_version_id)
        task_spec = session.get(
            StandardPackTaskSpecification, binding.task.standard_pack_task_spec_id
        )
        if exam is None or task_spec is None:
            raise WritingExecutionError("WRITING_SOURCE_NOT_ELIGIBLE")
        item = session.get(QuestionBankItemVersion, binding.question_bank_item_version_id)
        pack = session.get(StandardPackVersion, rubric.standard_pack_version_id)
        if (
            item is None
            or item.skill != QuestionBankSkill.WRITING
            or item.response_mode not in {PackResponseMode.TEXT, PackResponseMode.LONG_TEXT}
        ):
            raise WritingExecutionError("WRITING_SOURCE_NOT_ELIGIBLE")
        if (
            rubric.skill != QuestionBankSkill.WRITING
            or rubric.status not in {RubricVersionStatus.PUBLISHED, RubricVersionStatus.ACTIVE}
            or rubric.rubric.organisation_id != attempt.organisation_id
        ):
            raise WritingExecutionError("WRITING_RUBRIC_NOT_PUBLISHED")
        if (
            rubric.ai_assessment_policy.get("mode") != "AI_ASSISTED"
            or rubric.ai_assessment_policy.get("enabled") is not True
            or rubric.human_review_policy.get("required") is not True
        ):
            raise WritingExecutionError("EXPLICIT_AI_ASSISTED_POLICY_REQUIRED")
        if (
            rubric.task_spec_id != binding.task.standard_pack_task_spec_id
            or item.rubric_reference != str(rubric.id)
        ):
            raise WritingExecutionError("WRITING_RUBRIC_BINDING_MISMATCH")
        if (
            pack is None
            or not pack.configuration_checksum
            or str(attempt.runtime_plan.get("standard_pack_version_id")) != str(pack.id)
            or exam.standard_pack_version_id != pack.id
            or item.language != rubric.language
            or item.assessment_family != rubric.assessment_family
        ):
            raise WritingExecutionError("WRITING_PACK_MISMATCH")
        if canonical_checksum(RubricService.checksum_payload(rubric)) != rubric.checksum:
            raise WritingExecutionError("STALE_WRITING_RUBRIC")
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
            if response is not None
            else None
        )
        if (
            revision is None
            or response is None
            or revision.attempt_id != attempt.id
            or revision.payload_checksum != canonical_checksum(revision.payload)
            or response.current_payload != revision.payload
        ):
            raise WritingExecutionError("STALE_WRITING_RESPONSE")
        candidate_text = _text(revision.payload.get("text"), 100_000)
        if (
            response_revision_id is not None
            and response_revision_id != revision.id
            or response_checksum is not None
            and response_checksum != revision.payload_checksum
            or rubric_checksum is not None
            and rubric_checksum != rubric.checksum
        ):
            raise WritingExecutionError("STALE_WRITING_EVIDENCE")
        plan_item = next(
            (
                i
                for m in attempt.runtime_plan.get("modules", [])
                for s in m.get("sections", [])
                for t in s.get("tasks", [])
                for i in t.get("items", [])
                if i.get("binding_id") == str(binding.id)
            ),
            None,
        )
        if (
            plan_item is None
            or plan_item.get("item_version_id") != str(item.id)
            or plan_item.get("item_version_checksum") != item.checksum
            or canonical_checksum(attempt.runtime_plan) != attempt.runtime_plan_checksum
        ):
            raise WritingExecutionError("STALE_WRITING_ITEM")
        # No retrieval is invented. Explicit source-backed evidence must be
        # supported by a separately implemented, exact approved-source path.
        if (
            any(b.required for b in rubric.evidence_bindings)
            or rubric.evidence_policy.get("source_backed") is True
        ):
            raise WritingExecutionError("SOURCE_BACKED_WRITING_UNCONFIGURED")
        source = {
            "organisation_id": str(attempt.organisation_id),
            "candidate_user_id": str(attempt.candidate_user_id),
            "attempt_id": str(attempt.id),
            "exam_version_id": str(attempt.exam_version_id),
            "exam_version_checksum": exam.checksum,
            "runtime_plan_checksum": attempt.runtime_plan_checksum,
            "exam_version_item_binding_id": str(binding.id),
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
            "task_label": task_spec.name,
        }
        # Access candidate text only after the exact source was resolved.
        del candidate_text
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
            },
            "candidate_text": revision.payload["text"],
            "candidate_text_untrusted": True,
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
        cls, session: Session, job: WritingExecutionJob
    ) -> tuple[CandidateResponseRevision, RubricVersion, dict[str, Any]]:
        from app.writing_execution.registry import snapshot_registry_valid

        try:
            _, revision, rubric, source = cls.source(
                session,
                attempt_id=job.attempt_id,
                binding_id=job.exam_version_item_binding_id,
                rubric_version_id=job.rubric_version_id,
                response_revision_id=job.candidate_response_revision_id,
                response_checksum=job.response_checksum,
                rubric_checksum=job.rubric_checksum,
            )
        except ValueError as exc:
            raise WritingExecutionError("STALE_WRITING_EVIDENCE") from exc
        if (
            source != job.source_identity
            or canonical_checksum(job.execution_snapshot) != job.snapshot_checksum
        ):
            raise WritingExecutionError("STALE_WRITING_EVIDENCE")
        if not snapshot_registry_valid(session, job.execution_snapshot):
            raise WritingExecutionError("PROFILE_APPROVAL_INVALID")
        item = session.get(QuestionBankItemVersion, job.question_bank_item_version_id)
        assert item is not None
        model_input = cls.build_input(
            job.id, job.ai_run_id, revision, rubric, source, str(item.content.get("prompt", ""))
        )
        if canonical_checksum(model_input) != job.input_checksum:
            raise WritingExecutionError("STALE_WRITING_EVIDENCE")
        return revision, rubric, model_input

    @staticmethod
    def audit(session: Session, job: WritingExecutionJob, event: str) -> None:
        AuditService.record(
            session,
            TenantContext(
                user_id=job.requested_by_user_id,
                organisation_id=job.organisation_id,
                membership_id=job.requested_membership_id,
            ),
            event_type=event,
            entity_type="WRITING_EXECUTION_JOB",
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
        job: WritingExecutionJob,
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
        execution_profile_id: uuid.UUID,
        execution_profile_version: str,
        model_profile_id: uuid.UUID,
        prompt_version_id: uuid.UUID,
        agent_version_id: uuid.UUID,
        workflow_version_id: uuid.UUID,
        operation_id: uuid.UUID,
        settings: Settings,
    ) -> WritingExecutionJob:
        pilot.require_unrestricted(settings)
        from app.writing_execution.registry import (
            approved_profile_record,
            snapshot_execution_profile,
        )
        from app.writing_execution.runtime import validate_execution_configuration

        attempt, revision, rubric, source = cls.source(
            session,
            attempt_id=attempt_id,
            binding_id=binding_id,
            rubric_version_id=rubric_version_id,
            response_revision_id=candidate_response_revision_id,
            response_checksum=response_checksum,
            rubric_checksum=rubric_checksum,
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
            select(WritingExecutionJob).where(WritingExecutionJob.operation_id == operation_id)
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
                raise WritingExecutionError("WRITING_OPERATION_CONFLICT")
            return existing
        if (
            execution_profile_id != workflow_version_id
            or rubric.ai_assessment_policy.get("execution_profile_id") != str(execution_profile_id)
            or rubric.ai_assessment_policy.get("execution_profile_version")
            != execution_profile_version
        ):
            raise WritingExecutionError("WRITING_EXECUTION_POLICY_MISMATCH")
        snapshot = snapshot_execution_profile(
            session,
            model_profile_id=model_profile_id,
            prompt_version_id=prompt_version_id,
            agent_version_id=agent_version_id,
            workflow_version_id=workflow_version_id,
        )
        approval = approved_profile_record(session, snapshot)
        if approval is None or snapshot.get("profile_version") != execution_profile_version:
            raise WritingExecutionError("PROFILE_APPROVAL_INVALID")
        snapshot["approval"] = {
            "id": str(approval.id),
            "profile_version": execution_profile_version,
            "approved_by": str(approval.actor_id),
        }
        availability = validate_execution_configuration(snapshot, settings)
        if availability.get("availability") not in {"CONFIGURED", "TEST_ONLY"}:
            raise WritingExecutionError(
                str(availability.get("unavailable_reason") or "UNCONFIGURED")
            )
        active = session.scalar(
            select(WritingExecutionJob.id).where(
                WritingExecutionJob.attempt_id == attempt_id,
                WritingExecutionJob.exam_version_item_binding_id == binding_id,
                WritingExecutionJob.status.in_(ACTIVE_STATES),
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
            raise WritingExecutionError("WRITING_REVIEW_OR_EXECUTION_ALREADY_CURRENT")
        version = SubjectiveAssessmentService.create(
            session,
            context,
            attempt_id=attempt_id,
            binding_id=binding_id,
            rubric_version_id=rubric_version_id,
            _execution_origin=True,
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
            raise WritingExecutionError("WRITING_INPUT_TOO_LARGE")
        timestamp = now()
        assert context.membership_id is not None
        job = WritingExecutionJob(
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
        cls.audit(session, job, "writing_execution.requested")
        return job

    @classmethod
    def claim_next(
        cls,
        session: Session,
        *,
        worker_id: str,
        lease_seconds: int = 90,
        now: datetime | None = None,
    ) -> WritingExecutionJob | None:
        if pilot.active():
            return None
        timestamp = now or globals()["now"]()
        if not worker_id or len(worker_id) > 120 or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid Writing worker configuration")
        eligible = or_(
            and_(
                WritingExecutionJob.status.in_(("QUEUED", "RETRYABLE")),
                WritingExecutionJob.next_attempt_at <= timestamp,
            ),
            and_(
                WritingExecutionJob.status == "RUNNING",
                WritingExecutionJob.lease_expires_at <= timestamp,
            ),
        )
        while True:
            job = session.scalar(
                select(WritingExecutionJob)
                .where(eligible)
                .order_by(WritingExecutionJob.created_at, WritingExecutionJob.id)
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
                cls.audit(session, job, "writing_execution.failed")
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
                node_id="writing_proposal",
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
            cls.audit(session, job, "writing_execution.started")
            session.flush()
            return job

    @staticmethod
    def fenced(
        session: Session, job_id: uuid.UUID, claim_token: uuid.UUID
    ) -> WritingExecutionJob | None:
        job = session.scalar(
            select(WritingExecutionJob)
            .where(WritingExecutionJob.id == job_id)
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
        expected_keys = {
            "schema_version",
            "execution_id",
            "ai_run_id",
            "response_revision_id",
            "response_checksum",
            "rubric_version_id",
            "rubric_checksum",
            "criteria",
            "consistency",
        }
        if (
            not isinstance(proposal, dict)
            or set(proposal) != expected_keys
            or proposal.get("schema_version") != "1"
            or proposal.get("execution_id") != model_input["execution_id"]
            or proposal.get("ai_run_id") != model_input["ai_run_id"]
        ):
            raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
        if any(proposal.get(k) != v for k, v in model_input["source"].items()):
            raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
        criteria = proposal.get("criteria")
        expected = {c["code"]: c for c in model_input["rubric"]["criteria"]}
        levels = {level["code"] for level in model_input["rubric"]["performance_levels"]}
        if (
            not isinstance(criteria, list)
            or len(criteria) != len(expected)
            or len(criteria) > 50
            or any(not isinstance(c, dict) for c in criteria)
            or {c.get("criterion_code") for c in criteria} != set(expected)
        ):
            raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
        normalized = []
        for c in criteria:
            if set(c) != {
                "criterion_code",
                "proposed_points",
                "proposed_level",
                "confidence",
                "evidence",
                "rationale",
                "uncertainties",
                "disagreement",
            }:
                raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
            code = c["criterion_code"]
            raw = c["proposed_points"]
            if (
                not isinstance(raw, str)
                or re.fullmatch(r"(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,4})?", raw) is None
            ):
                raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
            points = Decimal(raw)
            if (
                not points.is_finite()
                or not 0 <= points <= Decimal(str(expected[code]["max_points"]))
                or c["proposed_level"] not in levels
                or c["confidence"] not in {"LOW", "MEDIUM", "HIGH"}
            ):
                raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
            _text(c["rationale"], 2000)
            if (
                not isinstance(c["uncertainties"], list)
                or len(c["uncertainties"]) > 20
                or any(not isinstance(v, str) or len(v) > 500 for v in c["uncertainties"])
                or c["disagreement"] != {}
            ):
                raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
            evidence = c["evidence"]
            text_value = model_input["candidate_text"]
            if not isinstance(evidence, list) or len(evidence) > 20:
                raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
            for ref in evidence:
                if (
                    not isinstance(ref, dict)
                    or set(ref) != {"start", "end", "quote"}
                    or type(ref["start"]) is not int
                    or type(ref["end"]) is not int
                    or not 0 <= ref["start"] < ref["end"] <= len(text_value)
                    or not isinstance(ref["quote"], str)
                    or len(ref["quote"]) > 2000
                    or text_value[ref["start"] : ref["end"]] != ref["quote"]
                ):
                    raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
            policy = model_input["rubric"]["evidence_policy"]
            if policy.get("required") and len(evidence) < int(policy.get("minimum_references", 1)):
                raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
            normalized.append(
                {
                    **copy.deepcopy(c),
                    "proposed_points": format(points, ".4f"),
                    "rationale": c["rationale"].strip(),
                }
            )
        consistency = proposal["consistency"]
        if (
            not isinstance(consistency, dict)
            or set(consistency) != {"status", "criterion_codes"}
            or consistency["status"] not in {"PASS", "WARNING"}
            or not isinstance(consistency["criterion_codes"], list)
            or len(consistency["criterion_codes"]) > 50
            or any(code not in expected for code in consistency["criterion_codes"])
        ):
            raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
        return {
            **copy.deepcopy(proposal),
            "criteria": sorted(normalized, key=lambda c: c["criterion_code"]),
        }

    @classmethod
    def fail_claim(
        cls, session: Session, *, job_id: uuid.UUID, claim_token: uuid.UUID, category: str
    ) -> WritingExecutionJob | None:
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
        cls.audit(session, job, "writing_execution.failed")
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
        provider_factory: ProviderFactory | None = None,
    ) -> WritingExecutionJob | None:
        from app.writing_execution.runtime import (
            build_writing_provider,
            validate_execution_configuration,
        )

        try:
            with factory() as session:
                job = cls.fenced(session, job_id, claim_token)
                if job is None:
                    return None
                _, _, model_input = cls.verify_source(session, job)
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
                    raise WritingExecutionError("UNCONFIGURED")
                session.commit()
            provider = (provider_factory or build_writing_provider)(snapshot, settings)
            if (provider.provider, provider.model_name, provider.adapter_version) != (
                snapshot["provider"],
                snapshot["model_name"],
                snapshot["adapter_version"],
            ):
                raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
            result = provider.generate(
                model_input,
                configuration={
                    **snapshot["configuration"],
                    "execution_id": str(job_id),
                    "input_checksum": canonical_checksum(model_input),
                },
                limits={"max_input_bytes": 200000, "max_output_bytes": 250000},
            )
            metadata = result.metadata
            if (
                (result.provider, result.model_name, result.adapter_version)
                != (snapshot["provider"], snapshot["model_name"], snapshot["adapter_version"])
                or not isinstance(metadata, dict)
                or metadata.get("execution_id") != str(job_id)
                or metadata.get("input_checksum") != canonical_checksum(model_input)
                or metadata.get("output_schema_version") != "1"
                or metadata.get("test_only") is not (snapshot["provider"] == "DETERMINISTIC_TEST")
                or not isinstance(result.provider_request_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,160}", result.provider_request_id) is None
            ):
                raise WritingExecutionError("INVALID_PROVIDER_OUTPUT")
            proposal = cls.validate_proposal(result.proposal, model_input)
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
                SubjectiveAssessmentService.propose(
                    session, context, job.assessment_version_id, proposal, ai_run_id=run.id
                )
                cls.audit(session, job, "writing_execution.completed")
                session.commit()
                return job
        except Exception as exc:  # noqa: BLE001
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
    ) -> WritingExecutionJob | None:
        pilot.require_unrestricted(settings)
        with session_factory() as session:
            job = session.get(WritingExecutionJob, job_id)
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

        thread = threading.Thread(target=renew, daemon=True, name="writing-lease")
        thread.start()
        try:
            return cls._execute_claim(
                session_factory,
                job_id=job_id,
                claim_token=claim_token,
                settings=settings,
                provider_factory=provider_factory,
            )
        finally:
            stop.set()
            thread.join(timeout=1)

    @staticmethod
    def can_retry(job: WritingExecutionJob) -> bool:
        return job.status == "RETRYABLE" and job.attempt_count < job.max_attempts

    @classmethod
    def retry(
        cls, session: Session, *, job: WritingExecutionJob, context: TenantContext
    ) -> WritingExecutionJob:
        cls.authorize(
            session,
            context,
            job.organisation_id,
            "subjective.assess",
            candidate_user_id=job.candidate_user_id,
        )
        if not cls.can_retry(job):
            raise WritingExecutionError("WRITING_RETRY_NOT_ALLOWED")
        cls.verify_source(session, job)
        job.next_attempt_at = now()
        cls.transition(job, "RETRYABLE", category=job.failure_category)
        cls.audit(session, job, "writing_execution.retry_requested")
        return job

    @classmethod
    def cancel(
        cls, session: Session, *, job: WritingExecutionJob, context: TenantContext
    ) -> WritingExecutionJob:
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
            raise WritingExecutionError("WRITING_CANCEL_NOT_ALLOWED")
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
        cls.audit(session, job, "writing_execution.cancelled")
        session.flush()
        return job

    @staticmethod
    def job_payload(
        session: Session, job: WritingExecutionJob, context: TenantContext
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
            WritingExecutionService.authorize(
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
            "can_retry": allowed and WritingExecutionService.can_retry(job),
            "can_cancel": allowed and job.status in ACTIVE_STATES,
            "assessment_mode": "AI_ASSISTED",
        }

    @classmethod
    def validate_handoff(
        cls,
        session: Session,
        version: SubjectiveAssessmentVersion,
        run_id: uuid.UUID,
        proposal: dict[str, Any],
    ) -> WritingExecutionJob:
        job = session.scalar(
            select(WritingExecutionJob).where(
                WritingExecutionJob.ai_run_id == run_id,
                WritingExecutionJob.assessment_version_id == version.id,
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
            raise WritingExecutionError("COMPLETED_WRITING_EXECUTION_REQUIRED")
        step = session.get(AIRunStep, job.active_step_id) if job.active_step_id else None
        snapshot = job.execution_snapshot
        if (
            step is None
            or step.run_id != run.id
            or step.status != AIRunStatus.SUCCEEDED
            or step.completed_at is None
            or step.input_checksum != job.input_checksum
            or step.output_checksum != job.output_checksum
            or step.provider_request_id != job.provider_request_id
            or str(step.model_profile_id) != snapshot["model_profile_id"]
            or str(step.prompt_version_id) != snapshot["prompt_version_id"]
            or str(step.agent_version_id) != snapshot["agent_version_id"]
            or str(run.workflow_version_id) != snapshot["workflow_version_id"]
            or str(run.agent_version_id) != snapshot["agent_version_id"]
        ):
            raise WritingExecutionError("COMPLETED_WRITING_EXECUTION_REQUIRED")
        _, _, model_input = cls.verify_source(session, job)
        if (
            job.output_checksum != canonical_checksum(proposal)
            or job.validated_proposal != proposal
            or canonical_checksum(cls.validate_proposal(proposal, model_input))
            != job.output_checksum
        ):
            raise WritingExecutionError("WRITING_EXECUTION_OUTPUT_MISMATCH")
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
            ]
        ):
            raise WritingExecutionError("WRITING_EXECUTION_SOURCE_MISMATCH")
        return job

    @staticmethod
    def execution_provenance(
        session: Session, version: SubjectiveAssessmentVersion
    ) -> dict[str, Any] | None:
        job = (
            session.scalar(
                select(WritingExecutionJob).where(
                    WritingExecutionJob.ai_run_id == version.ai_run_id
                )
            )
            if version.ai_run_id
            else None
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
            "candidate_response_revision_id": str(job.candidate_response_revision_id),
            "response_checksum": job.response_checksum,
            "rubric_version_id": str(job.rubric_version_id),
            "rubric_checksum": job.rubric_checksum,
            "test_only": snapshot["provider"] == "DETERMINISTIC_TEST",
        }
