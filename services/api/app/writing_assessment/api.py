from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.db import get_db_session
from app.models import (
    AgentVersion,
    AIRun,
    AssessmentRubric,
    CandidateResponseRevision,
    ExamVersionItemBinding,
    QuestionBankItemVersion,
    RubricVersion,
    StandardPackTaskSpecification,
    StandardPackVersion,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
)
from app.models.enums import HumanReviewStatus, QuestionBankSkill
from app.writing_assessment.governance_api import human_writing_router, review_projection
from app.writing_assessment.service import RubricError, RubricService, SubjectiveAssessmentService

writing_router = APIRouter(prefix="/api/v1/organisations", tags=["writing-assessment"])
writing_router.include_router(human_writing_router)


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, (AuthorizationError, PermissionError)):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


def _require_read(context: TenantContext, organisation_id: uuid.UUID) -> None:
    try:
        AuthorizationService.require_permission(context, "subjective.read", organisation_id)
    except AuthorizationError as exc:
        raise _error(exc) from exc


def _legacy_ai_review_eligible(session: Session, version: SubjectiveAssessmentVersion) -> bool:
    """Recognize pinned legacy association without claiming authentic inference."""
    if version.assessment_origin != "UNSPECIFIED" or version.status.value != "AI_PROPOSED":
        return False
    if not all(
        (
            version.ai_run_id,
            version.workflow_version_id,
            version.agent_version_id,
            version.prompt_version_id,
            version.model_profile_id,
        )
    ):
        return False
    run = session.get(AIRun, version.ai_run_id)
    agent = session.get(AgentVersion, version.agent_version_id)
    return bool(
        run
        and agent
        and run.workflow_version_id == version.workflow_version_id
        and run.agent_version_id == version.agent_version_id
        and agent.prompt_version_id == version.prompt_version_id
        and agent.model_profile_id == version.model_profile_id
        and agent.model_profile
        and agent.model_profile.purpose == "WRITING_ASSESSMENT"
    )


@writing_router.get("/{organisation_id}/subjective-assessments")
def list_assessments(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    _require_read(context, organisation_id)
    assessments = session.scalars(
        select(SubjectiveAssessment)
        .where(
            SubjectiveAssessment.organisation_id == organisation_id,
            SubjectiveAssessment.candidate_user_id != context.user_id,
        )
        .options(selectinload(SubjectiveAssessment.versions))
        .order_by(SubjectiveAssessment.created_at.desc())
    ).all()
    result: list[dict[str, Any]] = []
    for item in assessments:
        current = SubjectiveAssessmentService.current_version(item)
        rubric = session.get(RubricVersion, current.rubric_version_id) if current else None
        result.append(
            {
                "id": str(item.id),
                "attempt_id": str(item.attempt_id),
                "status": current.status.value if current else "CREATED",
                "current_version_id": str(current.id) if current else None,
                "assessment_origin": current.assessment_origin if current else "UNSPECIFIED",
                "skill": rubric.skill.value if rubric else None,
            }
        )
    return result


@writing_router.get("/{organisation_id}/subjective-assessments/{assessment_id}")
def get_assessment_workspace(
    organisation_id: uuid.UUID,
    assessment_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    _require_read(context, organisation_id)
    assessment = session.scalar(
        select(SubjectiveAssessment)
        .where(
            SubjectiveAssessment.id == assessment_id,
            SubjectiveAssessment.organisation_id == organisation_id,
        )
        .options(
            selectinload(SubjectiveAssessment.versions).selectinload(
                SubjectiveAssessmentVersion.criterion_assessments
            )
        )
    )
    if assessment is None:
        raise HTTPException(status_code=404, detail="assessment not found")
    try:
        SubjectiveAssessmentService.assert_reviewer_access(context, assessment)
    except RubricError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    current = SubjectiveAssessmentService.current_version(assessment)
    if current is None:
        raise HTTPException(status_code=404, detail="assessment version not found")
    revision = session.get(CandidateResponseRevision, current.candidate_response_revision_id)
    rubric = session.get(RubricVersion, current.rubric_version_id)
    binding = session.get(ExamVersionItemBinding, current.exam_version_item_binding_id)
    task = (
        session.get(StandardPackTaskSpecification, binding.task.standard_pack_task_spec_id)
        if binding
        else None
    )
    item = session.get(QuestionBankItemVersion, current.question_bank_item_version_id)
    return {
        "id": str(assessment.id),
        "version_id": str(current.id),
        "status": current.status.value,
        "checksum": current.checksum,
        "assessment_origin": current.assessment_origin,
        "ai_run_id": str(current.ai_run_id) if current.ai_run_id else None,
        "legacy_ai_review_eligible": _legacy_ai_review_eligible(session, current),
        "skill": rubric.skill.value if rubric else None,
        "assessor_id": current.provenance.get("assessed_by")
        if current.assessment_origin == "HUMAN"
        else None,
        "reviewer_id": current.provenance.get("reviewed_by")
        if current.assessment_origin == "HUMAN"
        else None,
        "provenance": current.provenance if current.assessment_origin == "HUMAN" else {},
        "attempt_id": str(current.attempt_id),
        "exam_version_id": str(current.exam_version_id),
        "exam_version_item_binding_id": str(current.exam_version_item_binding_id),
        "question_bank_item_version_id": str(current.question_bank_item_version_id),
        "candidate_response_revision_id": str(current.candidate_response_revision_id),
        "response_checksum": current.response_checksum,
        "rubric_checksum": current.rubric_checksum,
        "standard_pack_version_id": str(current.standard_pack_version_id),
        "task_spec_id": str(rubric.task_spec_id) if rubric and rubric.task_spec_id else None,
        "task_label": task.name if task else "Writing task",
        "task_prompt": str(item.content.get("prompt", ""))
        if item
        and AuthorizationService.has_permission(context, "question_bank.read", organisation_id)
        else "",
        "task_prompt_available": bool(
            item
            and AuthorizationService.has_permission(context, "question_bank.read", organisation_id)
        ),
        "proposed_score": str(current.proposed_score)
        if current.proposed_score is not None
        else None,
        "approved_score": str(current.approved_score)
        if current.approved_score is not None
        else None,
        "review": review_projection(
            session,
            "SUBJECTIVE_ASSESSMENT_VERSION",
            current.id,
            review_type="HUMAN_WRITING_ASSESSMENT"
            if current.assessment_origin == "HUMAN"
            else None,
        ),
        "candidate_text": (revision.payload.get("text") if revision else ""),
        "rubric": {
            "id": str(rubric.id),
            "checksum": rubric.checksum,
            "criteria": rubric.criteria,
            "performance_levels": rubric.performance_levels,
            "aggregation_policy": rubric.aggregation_policy,
            "evidence_policy": rubric.evidence_policy,
        }
        if rubric
        else None,
        "criteria": [
            {
                "criterion_code": item.criterion_code,
                "proposed_level": item.proposed_level,
                "proposed_points": str(item.proposed_points),
                "approved_level": item.approved_level,
                "approved_points": str(item.approved_points)
                if item.approved_points is not None
                else None,
                "evidence": item.evidence,
                "rationale": item.rationale,
                "confidence": item.confidence.value if item.confidence else None,
                "disagreement": item.disagreement,
            }
            for item in current.criterion_assessments
        ],
    }


@writing_router.post("/{organisation_id}/rubrics")
def create_rubric(
    organisation_id: uuid.UUID,
    payload: dict[str, Any],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        AuthorizationService.require_permission(context, "rubric.manage", organisation_id)
        rubric = RubricService.create_rubric(
            session,
            code=str(payload["code"]),
            name=str(payload["name"]),
            organisation_id=organisation_id,
            context=context,
        )
        session.commit()
        return {"id": str(rubric.id), "code": rubric.code, "status": rubric.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@writing_router.post("/{organisation_id}/rubrics/{rubric_id}/versions")
def create_rubric_version(
    organisation_id: uuid.UUID,
    rubric_id: uuid.UUID,
    payload: dict[str, Any],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        AuthorizationService.require_permission(context, "rubric.manage", organisation_id)
        rubric = session.get(AssessmentRubric, rubric_id)
        pack = session.get(StandardPackVersion, uuid.UUID(str(payload["standard_pack_version_id"])))
        if rubric is None or rubric.organisation_id != organisation_id or pack is None:
            raise RubricError("rubric or StandardPackVersion is not available")
        version = RubricService.create_version(
            session,
            rubric=rubric,
            standard_pack=pack,
            version_label=str(payload["version_label"]),
            criteria=payload["criteria"],
            performance_levels=payload["performance_levels"],
            aggregation_policy=payload["aggregation_policy"],
            evidence_policy=payload["evidence_policy"],
            ai_assessment_policy=payload.get("ai_assessment_policy", {"all_require_review": True}),
            human_review_policy=payload.get("human_review_policy", {"required": True}),
            evidence_bindings=payload.get("evidence_bindings", []),
            skill=QuestionBankSkill(str(payload.get("skill", QuestionBankSkill.WRITING.value))),
            task_spec_id=uuid.UUID(str(payload["task_spec_id"]))
            if payload.get("task_spec_id")
            else None,
            context=context,
        )
        session.commit()
        return {"id": str(version.id), "status": version.status.value, "checksum": version.checksum}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@writing_router.post("/{organisation_id}/subjective-assessments")
def create_assessment(
    organisation_id: uuid.UUID,
    payload: dict[str, Any],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        version = SubjectiveAssessmentService.create(
            session,
            context,
            attempt_id=uuid.UUID(str(payload["attempt_id"])),
            binding_id=uuid.UUID(str(payload["exam_version_item_binding_id"])),
            rubric_version_id=uuid.UUID(str(payload["rubric_version_id"])),
            skill=QuestionBankSkill(str(payload.get("skill", QuestionBankSkill.WRITING.value))),
            media_asset_id=uuid.UUID(str(payload["media_asset_id"]))
            if payload.get("media_asset_id")
            else None,
            transcription_version_id=uuid.UUID(str(payload["transcription_version_id"]))
            if payload.get("transcription_version_id")
            else None,
        )
        session.commit()
        return {"id": str(version.id), "status": version.status.value, "checksum": version.checksum}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@writing_router.post("/{organisation_id}/subjective-assessments/{version_id}/propose")
def propose_assessment(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: dict[str, Any],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        version = SubjectiveAssessmentService.propose(
            session,
            context,
            version_id,
            payload,
            ai_run_id=uuid.UUID(str(payload["ai_run_id"])) if payload.get("ai_run_id") else None,
        )
        session.commit()
        return {"id": str(version.id), "status": version.status.value, "checksum": version.checksum}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@writing_router.post("/{organisation_id}/subjective-assessments/{version_id}/approve")
def approve_assessment(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: Optional[dict[str, Any]] = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        version = SubjectiveAssessmentService.approve(
            session, context, version_id, (payload or {}).get("corrections")
        )
        session.commit()
        return {
            "id": str(version.id),
            "status": version.status.value,
            "approved_score": str(version.approved_score),
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@writing_router.post("/{organisation_id}/subjective-assessments/{version_id}/decision")
def decide_assessment(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: dict[str, Any],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        decision = HumanReviewStatus(str(payload["decision"]))
        version = SubjectiveAssessmentService.decide(
            session, context, version_id, decision, payload.get("reason")
        )
        session.commit()
        return {"id": str(version.id), "status": version.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@writing_router.get("/{organisation_id}/subjective-assessments/{assessment_id}/versions")
def list_assessment_versions(
    organisation_id: uuid.UUID,
    assessment_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        AuthorizationService.require_permission(context, "subjective.read", organisation_id)
        from app.models import SubjectiveAssessment

        assessment = session.get(SubjectiveAssessment, assessment_id)
        if assessment is None or assessment.organisation_id != organisation_id:
            raise HTTPException(status_code=404, detail="assessment not found")
        SubjectiveAssessmentService.assert_reviewer_access(context, assessment)
        current = SubjectiveAssessmentService.current_version(assessment)
        return {
            "current_version_id": str(current.id) if current else None,
            "versions": [
                {
                    "id": str(item.id),
                    "version_number": item.version_number,
                    "status": item.status.value,
                    "checksum": item.checksum,
                }
                for item in SubjectiveAssessmentService.historical_versions(assessment)
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@writing_router.get(
    "/{organisation_id}/subjective-assessments/versions/{version_id}/diff/{other_version_id}"
)
def diff_assessment_versions(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    other_version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, bool]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        AuthorizationService.require_permission(context, "subjective.read", organisation_id)
        from app.models import SubjectiveAssessment, SubjectiveAssessmentVersion

        left = session.get(SubjectiveAssessmentVersion, version_id)
        right = session.get(SubjectiveAssessmentVersion, other_version_id)
        if left is None or right is None:
            raise HTTPException(status_code=404, detail="assessment version not found")
        parent = session.get(SubjectiveAssessment, left.subjective_assessment_id)
        if (
            parent is None
            or parent.organisation_id != organisation_id
            or right.subjective_assessment_id != parent.id
        ):
            raise HTTPException(status_code=404, detail="assessment version not found")
        SubjectiveAssessmentService.assert_reviewer_access(context, parent)
        return SubjectiveAssessmentService.diff(left, right)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc
