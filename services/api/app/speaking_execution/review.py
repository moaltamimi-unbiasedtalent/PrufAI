"""Human judgment over executed Speaking proposals, using existing review roots."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import TenantContext
from app.models import (
    HumanReviewRequest,
    RubricVersion,
    SpeakingExecutionJob,
    SubjectiveAssessmentVersion,
    WritingGovernanceOperation,
)
from app.models.enums import HumanReviewStatus, SubjectiveAssessmentStatus
from app.speaking_execution.execution import SpeakingExecutionError, SpeakingExecutionService
from app.writing_assessment.service import SubjectiveAssessmentService, canonical_checksum

_CORRECTION_TOKEN = object()


def authorize_correction(
    session: Session,
    context: TenantContext,
    source: SubjectiveAssessmentVersion,
    review: HumanReviewRequest,
    metadata: dict[str, Any] | None,
    token: object | None,
) -> None:
    """Only the explicit governed decision may create its approved successor."""
    if token is not _CORRECTION_TOKEN or not isinstance(metadata, dict):
        raise SpeakingExecutionError("GOVERNED_SPEAKING_REVIEW_REQUIRED")
    job = session.scalar(
        select(SpeakingExecutionJob).where(SpeakingExecutionJob.assessment_version_id == source.id)
    )
    if job is None:
        raise SpeakingExecutionError("SPEAKING_EXECUTION_NOT_FOUND")
    context = SpeakingExecutionService.authorize(
        session,
        context,
        job.organisation_id,
        "subjective.review",
        candidate_user_id=job.candidate_user_id,
    )
    SpeakingExecutionService.validate_handoff(
        session, source, job.ai_run_id, job.validated_proposal
    )
    rubric = session.get(RubricVersion, job.rubric_version_id)
    current = SubjectiveAssessmentService.current_version(source.assessment)
    if (
        rubric is None
        or rubric.human_review_policy.get("required") is not True
        or source.status is not SubjectiveAssessmentStatus.AI_PROPOSED
        or current is None
        or current.id != source.id
        or review.entity_type != "SUBJECTIVE_ASSESSMENT_VERSION"
        or review.entity_id != source.id
        or review.ai_run_id != job.ai_run_id
        or review.status is not HumanReviewStatus.PENDING
        or review.reviewed_checksum != source.checksum
        or metadata.get("source_version_id") != str(source.id)
        or metadata.get("source_checksum") != source.checksum
        or metadata.get("reviewed_by") != str(context.user_id)
        or not metadata.get("review_operation_id")
        or not isinstance(metadata.get("review_reason"), str)
        or not metadata["review_reason"].strip()
    ):
        raise SpeakingExecutionError("STALE_SPEAKING_REVIEW")
    if rubric.human_review_policy.get("separation_of_duties") is True and context.user_id in {
        job.requested_by_user_id,
        review.requested_by,
    }:
        raise SpeakingExecutionError("INDEPENDENT_SPEAKING_REVIEWER_REQUIRED")


def decide(
    session: Session,
    context: TenantContext,
    version_id: uuid.UUID,
    *,
    decision: HumanReviewStatus,
    corrections: dict[str, dict[str, Any]] | None = None,
    operation_id: uuid.UUID | None = None,
    expected_checksum: str | None = None,
    reason: str | None = None,
) -> SubjectiveAssessmentVersion:
    version = session.scalar(
        select(SubjectiveAssessmentVersion)
        .where(SubjectiveAssessmentVersion.id == version_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    job = session.scalar(
        select(SpeakingExecutionJob).where(SpeakingExecutionJob.assessment_version_id == version_id)
    )
    if version is None or job is None:
        raise SpeakingExecutionError("SPEAKING_EXECUTION_NOT_FOUND")
    context = SpeakingExecutionService.authorize(
        session,
        context,
        job.organisation_id,
        "subjective.review",
        candidate_user_id=job.candidate_user_id,
    )
    if (
        operation_id is None
        or expected_checksum is None
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 2000
    ):
        raise SpeakingExecutionError("EXPLICIT_SPEAKING_REVIEW_REQUIRED")
    if decision not in {
        HumanReviewStatus.APPROVED,
        HumanReviewStatus.REJECTED,
        HumanReviewStatus.CHANGES_REQUESTED,
    }:
        raise SpeakingExecutionError("SPEAKING_REVIEW_DECISION_INVALID")
    payload = {
        "expected_checksum": expected_checksum,
        "decision": decision.value,
        "corrections": corrections,
        "reason": reason,
    }
    action = "speaking_execution.review_" + decision.value.lower()
    receipt = session.scalar(
        select(WritingGovernanceOperation).where(
            WritingGovernanceOperation.organisation_id == job.organisation_id,
            WritingGovernanceOperation.operation_id == operation_id,
        )
    )
    if receipt:
        if (
            receipt.actor_id != context.user_id
            or receipt.target_id != version.id
            or receipt.action != action
            or receipt.request_checksum != canonical_checksum(payload)
        ):
            raise SpeakingExecutionError("SPEAKING_REVIEW_OPERATION_CONFLICT")
        result = session.get(SubjectiveAssessmentVersion, receipt.result_id)
        if result is None:
            raise SpeakingExecutionError("SPEAKING_REVIEW_RESULT_UNAVAILABLE")
        return result
    SpeakingExecutionService.validate_handoff(
        session, version, job.ai_run_id, job.validated_proposal
    )
    rubric = session.get(RubricVersion, job.rubric_version_id)
    assert rubric is not None
    if rubric.human_review_policy.get("required") is not True:
        raise SpeakingExecutionError("MANDATORY_HUMAN_REVIEW_REQUIRED")
    review = session.scalar(
        select(HumanReviewRequest)
        .where(
            HumanReviewRequest.entity_type == "SUBJECTIVE_ASSESSMENT_VERSION",
            HumanReviewRequest.entity_id == version.id,
        )
        .with_for_update()
    )
    if (
        version.status != SubjectiveAssessmentStatus.AI_PROPOSED
        or version.checksum != expected_checksum
        or review is None
        or review.status != HumanReviewStatus.PENDING
        or review.reviewed_checksum != expected_checksum
        or review.ai_run_id != job.ai_run_id
    ):
        raise SpeakingExecutionError("STALE_SPEAKING_REVIEW")
    if rubric.human_review_policy.get("separation_of_duties") is True and context.user_id in {
        job.requested_by_user_id,
        review.requested_by,
    }:
        raise SpeakingExecutionError("INDEPENDENT_SPEAKING_REVIEWER_REQUIRED")
    latest = SubjectiveAssessmentService.current_version(version.assessment)
    if latest is None or latest.id != version.id:
        raise SpeakingExecutionError("STALE_SPEAKING_REVIEW")
    timestamp = datetime.now(timezone.utc)
    if decision == HumanReviewStatus.APPROVED:
        expected = {c["code"]: c for c in rubric.criteria}
        levels = {v["code"] for v in rubric.performance_levels}
        if not isinstance(corrections, dict) or set(corrections) != set(expected):
            raise SpeakingExecutionError("COMPLETE_HUMAN_CRITERION_DECISIONS_REQUIRED")
        for code, value in corrections.items():
            if (
                not isinstance(value, dict)
                or set(value) != {"approved_points", "approved_level", "rationale", "evidence"}
                or not isinstance(value["approved_points"], str)
            ):
                raise SpeakingExecutionError("COMPLETE_HUMAN_CRITERION_DECISIONS_REQUIRED")
            from app.speaking_execution.execution import _text

            if (
                re.fullmatch(r"(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,4})?", value["approved_points"])
                is None
            ):
                raise SpeakingExecutionError("SPEAKING_REVIEW_POINTS_INVALID")
            _text(value["rationale"], 2000)
            refs = value["evidence"]
            minimum = rubric.evidence_policy.get("minimum_references", 1)
            if not isinstance(refs, list) or not minimum <= len(refs) <= 10:
                raise SpeakingExecutionError("SPEAKING_REVIEW_EVIDENCE_INVALID")
            for ref in refs:
                if (
                    not isinstance(ref, dict)
                    or set(ref)
                    != {"type", "candidate_media_asset_id", "audio_sha256", "start_ms", "end_ms"}
                    or ref["type"] != "AUDIO"
                    or ref["candidate_media_asset_id"] != str(job.candidate_media_asset_id)
                    or ref["audio_sha256"] != job.audio_sha256
                    or type(ref["start_ms"]) is not int
                    or type(ref["end_ms"]) is not int
                    or not 0 <= ref["start_ms"] < ref["end_ms"] <= job.verified_duration_ms
                ):
                    raise SpeakingExecutionError("SPEAKING_REVIEW_EVIDENCE_INVALID")
            try:
                points = Decimal(value["approved_points"])
            except Exception as exc:
                raise SpeakingExecutionError("SPEAKING_REVIEW_POINTS_INVALID") from exc
            if (
                not points.is_finite()
                or not 0 <= points <= Decimal(str(expected[code]["max_points"]))
                or points != points.quantize(Decimal("0.0001"))
                or value["approved_level"] not in levels
            ):
                raise SpeakingExecutionError("SPEAKING_REVIEW_POINTS_INVALID")
        result = SubjectiveAssessmentService.correct(
            session,
            context,
            version,
            corrections,
            review,
            _review_metadata={
                "review_reason": reason.strip(),
                "review_operation_id": str(operation_id),
                "reviewed_at": timestamp.isoformat(),
                "reviewed_by": str(context.user_id),
                "source_version_id": str(version.id),
                "source_checksum": expected_checksum,
            },
            _execution_review_token=_CORRECTION_TOKEN,
        )
    else:
        if corrections:
            raise SpeakingExecutionError("SPEAKING_REVIEW_DECISION_INVALID")
        version.status = (
            SubjectiveAssessmentStatus.REJECTED
            if decision == HumanReviewStatus.REJECTED
            else SubjectiveAssessmentStatus.CHANGES_REQUESTED
        )
        review.status = decision
        review.decided_by = context.user_id
        review.decided_at = timestamp
        review.decision = decision.value
        review.reason = reason.strip()
        result = version
    review.reason = reason.strip()
    session.add(
        WritingGovernanceOperation(
            organisation_id=job.organisation_id,
            operation_id=operation_id,
            actor_id=context.user_id,
            action=action,
            target_id=version.id,
            request_checksum=canonical_checksum(payload),
            result_id=result.id,
            created_at=timestamp,
        )
    )
    AuditService.record(
        session,
        context,
        event_type=action,
        entity_type="SubjectiveAssessmentVersion",
        entity_id=str(result.id),
        organisation_id=job.organisation_id,
        require_audit_permission=False,
        metadata={
            "operation_id": str(operation_id),
            "execution_job_id": str(job.id),
            "source_version_id": str(version.id),
            "reviewed_checksum": expected_checksum,
            "assessment_origin": "AI",
            "test_only": job.execution_snapshot["provider"] == "DETERMINISTIC_TEST",
        },
    )
    session.flush()
    return result
