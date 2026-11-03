from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.authorization.membership import require_active_tenant_permission
from app.db import get_db_session
from app.models import (
    AssessmentRubric,
    CandidateExamAttempt,
    ExamVersionItemBinding,
    QuestionBankItemVersion,
    RubricVersion,
    StandardPackTaskSpecification,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
)
from app.models.enums import (
    CandidateAttemptStatus,
    HumanReviewStatus,
    QuestionBankSkill,
    RubricVersionStatus,
)
from app.writing_assessment.governance import (
    HumanWritingService,
    RubricGovernanceService,
    review_for,
)
from app.writing_assessment.service import SubjectiveAssessmentService

human_writing_router = APIRouter()


def _context(principal: Principal, organisation_id: uuid.UUID, permission: str) -> TenantContext:
    context = principal.tenant_context
    if context is None or context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        AuthorizationService.require_permission(context, permission, organisation_id)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return context


def _failure(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=403 if isinstance(exc, (PermissionError, AuthorizationError)) else 409,
        detail=str(exc),
    )


def review_projection(
    session: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    *,
    review_type: str | None = None,
) -> dict[str, Any] | None:
    review = review_for(session, entity_type, entity_id, review_type=review_type)
    if review is None:
        return None
    return {
        "id": str(review.id),
        "status": review.status.value,
        "requested_by": str(review.requested_by) if review.requested_by else None,
        "requested_at": review.requested_at.isoformat(),
        "decided_by": str(review.decided_by) if review.decided_by else None,
        "decided_at": review.decided_at.isoformat() if review.decided_at else None,
        "reviewed_checksum": review.reviewed_checksum,
    }


def rubric_projection(session: Session, version: RubricVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "rubric_id": str(version.rubric_id),
        "name": version.rubric.name,
        "code": version.rubric.code,
        "version_label": version.version_label,
        "status": version.status.value,
        "checksum": version.checksum,
        "skill": version.skill.value,
        "language": version.language,
        "assessment_family": version.assessment_family.value,
        "cefr_levels": list(version.cefr_levels),
        "standard_pack_version_id": str(version.standard_pack_version_id),
        "task_spec_id": str(version.task_spec_id) if version.task_spec_id else None,
        "criteria": version.criteria,
        "performance_levels": version.performance_levels,
        "aggregation_policy": version.aggregation_policy,
        "evidence_policy": version.evidence_policy,
        "ai_assessment_policy": version.ai_assessment_policy,
        "human_review_policy": version.human_review_policy,
        "approved_by_user_id": str(version.approved_by_user_id)
        if version.approved_by_user_id
        else None,
        "approved_at": version.approved_at.isoformat() if version.approved_at else None,
        "published_by_user_id": str(version.published_by_user_id)
        if version.published_by_user_id
        else None,
        "published_at": version.published_at.isoformat() if version.published_at else None,
        "review": review_projection(
            session, "RUBRIC_VERSION", version.id, review_type="RUBRIC_PUBLICATION"
        ),
    }


def assessment_projection(version: SubjectiveAssessmentVersion) -> dict[str, Any]:
    return {
        "id": str(version.subjective_assessment_id),
        "version_id": str(version.id),
        "status": version.status.value,
        "checksum": version.checksum,
        "assessment_origin": version.assessment_origin,
        "approved_score": str(version.approved_score)
        if version.approved_score is not None
        else None,
    }


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: uuid.UUID
    expected_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class InitializeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: uuid.UUID
    attempt_id: uuid.UUID
    exam_version_item_binding_id: uuid.UUID
    rubric_version_id: uuid.UUID
    response_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    rubric_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class ProposalRequest(OperationRequest):
    criteria: list[dict[str, Any]] = Field(min_length=1, max_length=50)


class DecisionRequest(OperationRequest):
    decision: HumanReviewStatus
    reason: str = Field(min_length=1, max_length=2000)


@human_writing_router.get("/{organisation_id}/rubrics")
def list_rubrics(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal, organisation_id, "rubric.read")
    speaking_allowed = True
    try:
        require_active_tenant_permission(session, context, "rubric.read")
    except AuthorizationError:
        speaking_allowed = False
    rubrics = session.scalars(
        select(AssessmentRubric)
        .where(AssessmentRubric.organisation_id == organisation_id)
        .order_by(AssessmentRubric.code)
        .limit(100)
    ).all()
    return [
        {
            "id": str(rubric.id),
            "code": rubric.code,
            "name": rubric.name,
            "versions": [
                rubric_projection(session, version)
                for version in rubric.versions
                if version.skill is not QuestionBankSkill.SPEAKING or speaking_allowed
            ],
        }
        for rubric in rubrics
        if speaking_allowed
        or any(version.skill is not QuestionBankSkill.SPEAKING for version in rubric.versions)
    ]


@human_writing_router.get("/{organisation_id}/rubrics/{rubric_id}/versions/{version_id}")
def get_rubric(
    organisation_id: uuid.UUID,
    rubric_id: uuid.UUID,
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "rubric.read")
    version = session.get(RubricVersion, version_id)
    if (
        version is None
        or version.rubric_id != rubric_id
        or version.rubric.organisation_id != organisation_id
    ):
        raise HTTPException(status_code=404, detail="rubric version not found")
    if version.skill is QuestionBankSkill.SPEAKING:
        try:
            require_active_tenant_permission(session, context, "rubric.read")
        except AuthorizationError as exc:
            raise _failure(exc) from exc
    return rubric_projection(session, version)


@human_writing_router.post("/{organisation_id}/rubrics/{rubric_id}/versions/{version_id}/{action}")
def rubric_action(
    organisation_id: uuid.UUID,
    rubric_id: uuid.UUID,
    version_id: uuid.UUID,
    action: str,
    payload: OperationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "rubric.manage")
    actions = {"request-review": "review_requested", "approve": "approved", "publish": "published"}
    if action not in actions:
        raise HTTPException(status_code=404, detail="rubric action not found")
    version = session.get(RubricVersion, version_id)
    if (
        version is None
        or version.rubric_id != rubric_id
        or version.rubric.organisation_id != organisation_id
    ):
        raise HTTPException(status_code=404, detail="rubric version not found")
    try:
        version = RubricGovernanceService.transition(
            session,
            context,
            version_id,
            action=actions[action],
            operation_id=payload.operation_id,
            expected_checksum=payload.expected_checksum,
        )
        session.commit()
        return rubric_projection(session, version)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _failure(exc) from exc


@human_writing_router.get("/{organisation_id}/writing-review-options")
def writing_review_options(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "subjective.read")
    _context(principal, organisation_id, "subjective.assess")
    attempts = session.scalars(
        select(CandidateExamAttempt)
        .where(
            CandidateExamAttempt.organisation_id == organisation_id,
            CandidateExamAttempt.candidate_user_id != context.user_id,
            CandidateExamAttempt.status == CandidateAttemptStatus.SUBMITTED,
        )
        .order_by(CandidateExamAttempt.created_at.desc())
        .limit(100)
    ).all()
    submissions = []
    for attempt in attempts:
        for response in attempt.responses:
            binding = session.get(ExamVersionItemBinding, response.exam_version_item_binding_id)
            item = (
                session.get(QuestionBankItemVersion, binding.question_bank_item_version_id)
                if binding
                else None
            )
            if (
                item is None
                or binding is None
                or item.skill is not QuestionBankSkill.WRITING
                or not response.revisions
            ):
                continue
            existing = session.scalar(
                select(SubjectiveAssessment).where(
                    SubjectiveAssessment.attempt_id == attempt.id,
                    SubjectiveAssessment.exam_version_item_binding_id == binding.id,
                )
            )
            current = SubjectiveAssessmentService.current_version(existing) if existing else None
            if current and current.status.value not in {
                "REJECTED",
                "CHANGES_REQUESTED",
                "FAILED",
                "VOIDED",
            }:
                continue
            revision = max(response.revisions, key=lambda row: row.revision_number)
            task_id = binding.task.standard_pack_task_spec_id
            task = session.get(StandardPackTaskSpecification, task_id)
            submissions.append(
                {
                    "attempt_id": str(attempt.id),
                    "exam_version_item_binding_id": str(binding.id),
                    "candidate_response_revision_id": str(revision.id),
                    "response_checksum": revision.payload_checksum,
                    "question_bank_item_version_id": str(item.id),
                    "rubric_version_id": item.rubric_reference,
                    "standard_pack_version_id": str(
                        attempt.runtime_plan["standard_pack_version_id"]
                    ),
                    "task_spec_id": str(task_id),
                    "task_label": task.name if task else "Writing task",
                }
            )
    rubrics = session.scalars(
        select(RubricVersion)
        .join(AssessmentRubric)
        .where(
            AssessmentRubric.organisation_id == organisation_id,
            RubricVersion.skill == QuestionBankSkill.WRITING,
            RubricVersion.status.in_([RubricVersionStatus.PUBLISHED, RubricVersionStatus.ACTIVE]),
        )
        .limit(100)
    ).all()
    return {
        "submissions": submissions[:100],
        "rubrics": [rubric_projection(session, version) for version in rubrics],
    }


@human_writing_router.post("/{organisation_id}/subjective-assessments/human")
def initialize_human(
    organisation_id: uuid.UUID,
    payload: InitializeRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "subjective.assess")
    try:
        version = HumanWritingService.initialize(
            session,
            context,
            operation_id=payload.operation_id,
            attempt_id=payload.attempt_id,
            binding_id=payload.exam_version_item_binding_id,
            rubric_version_id=payload.rubric_version_id,
            response_checksum=payload.response_checksum,
            rubric_checksum=payload.rubric_checksum,
        )
        session.commit()
        return assessment_projection(version)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _failure(exc) from exc


@human_writing_router.post("/{organisation_id}/subjective-assessments/{version_id}/human-proposal")
def propose_human(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: ProposalRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "subjective.assess")
    try:
        version = HumanWritingService.propose(
            session,
            context,
            version_id,
            operation_id=payload.operation_id,
            expected_checksum=payload.expected_checksum,
            criteria=payload.criteria,
        )
        session.commit()
        return assessment_projection(version)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _failure(exc) from exc


@human_writing_router.post("/{organisation_id}/subjective-assessments/{version_id}/human-approve")
def approve_human(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: OperationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "subjective.review")
    try:
        version = HumanWritingService.approve(
            session,
            context,
            version_id,
            operation_id=payload.operation_id,
            expected_checksum=payload.expected_checksum,
        )
        session.commit()
        return assessment_projection(version)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _failure(exc) from exc


@human_writing_router.post("/{organisation_id}/subjective-assessments/{version_id}/human-decision")
def decide_human(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: DecisionRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "subjective.review")
    try:
        version = HumanWritingService.decide(
            session,
            context,
            version_id,
            operation_id=payload.operation_id,
            expected_checksum=payload.expected_checksum,
            decision=payload.decision,
            reason=payload.reason,
        )
        session.commit()
        return assessment_projection(version)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _failure(exc) from exc
