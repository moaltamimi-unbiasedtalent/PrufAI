from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, TenantContext
from app.authorization.membership import require_active_tenant_permission
from app.db import get_db_session
from app.models import (
    CandidateMediaAsset,
    CandidateResponseRevision,
    HumanReviewRequest,
    QuestionBankItemVersion,
    RubricVersion,
    SpeakingExecutionJob,
    StandardPackTaskSpecification,
    SubjectiveAssessment,
    SubjectiveAssessmentVersion,
)
from app.speaking_assessment.governance_api import human_speaking_router, speaking_detail
from app.speaking_assessment.service import SpeakingAssessmentError, SpeakingAssessmentService
from app.speaking_audio.api import asset_projection
from app.speaking_execution.api import router as execution_router
from app.speaking_execution.execution import SpeakingExecutionService
from app.writing_assessment.governance_api import assessment_projection, rubric_projection
from app.writing_assessment.service import RubricError, SubjectiveAssessmentService

router = APIRouter(prefix="/api/v1/organisations", tags=["speaking-assessment"])
router.include_router(human_speaking_router)

router.include_router(execution_router)


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


def _assert_reviewer(session: Session, context: TenantContext, organisation_id: uuid.UUID) -> None:
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        require_active_tenant_permission(session, context, "subjective.read", organisation_id)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _executed_detail(
    session: Session,
    context: TenantContext,
    assessment: SubjectiveAssessment,
    version: SubjectiveAssessmentVersion,
    current: SubjectiveAssessmentVersion | None,
) -> dict[str, Any] | None:
    job = (
        session.scalar(
            select(SpeakingExecutionJob).where(
                or_(
                    SpeakingExecutionJob.assessment_version_id == version.id,
                    SpeakingExecutionJob.ai_run_id == version.ai_run_id,
                )
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
    SpeakingExecutionService.authorize(
        session,
        context,
        job.organisation_id,
        "subjective.read",
        candidate_user_id=job.candidate_user_id,
    )
    asset = session.get(CandidateMediaAsset, job.candidate_media_asset_id)
    rubric = session.get(RubricVersion, job.rubric_version_id)
    revision = session.get(CandidateResponseRevision, job.candidate_response_revision_id)
    if (
        asset is None
        or rubric is None
        or revision is None
        or version.candidate_media_asset_id != asset.id
        or version.audio_sha256 != job.audio_sha256
    ):
        raise SpeakingAssessmentError("Speaking execution evidence is unavailable")
    task = session.get(StandardPackTaskSpecification, rubric.task_spec_id)
    item = session.get(QuestionBankItemVersion, job.question_bank_item_version_id)
    audio = asset_projection(session, asset)
    audio["candidate_response_revision_id"] = str(revision.id)
    review = session.scalar(
        select(HumanReviewRequest).where(
            HumanReviewRequest.entity_type == "SUBJECTIVE_ASSESSMENT_VERSION",
            HumanReviewRequest.entity_id == job.assessment_version_id,
            HumanReviewRequest.review_type == "SPEAKING_ASSESSMENT",
        )
    )
    return {
        **assessment_projection(version),
        "assessment_id": str(assessment.id),
        "assessment_version_id": str(version.id),
        "version_id": str(version.id),
        "status": version.status.value,
        "assessment_origin": version.assessment_origin,
        "checksum": version.checksum,
        "version_number": version.version_number,
        "is_current_version": version is current,
        "current_version_id": str(current.id) if current else None,
        **{
            key: value
            for key, value in job.source_identity.items()
            if key not in {"candidate_user_id", "organisation_id", "recording_policy"}
        },
        "task_spec_id": str(rubric.task_spec_id),
        "task_label": task.name if task else "Speaking task",
        "task_prompt": str(item.content.get("prompt", ""))
        if item and "question_bank.read" in context.permissions
        else "",
        "task_prompt_available": bool(item and "question_bank.read" in context.permissions),
        "assessment_mode": "AI_ASSISTED",
        "evidence_mode": "AUDIO",
        "original_audio_primary_evidence": True,
        "derived_transcription": False,
        "speech_transcription_version_id": None,
        "transcript_checksum": None,
        "evidence_modes": version.evidence_mode_summary,
        "ai_run_id": str(job.ai_run_id),
        "execution_provenance": SpeakingExecutionService.execution_provenance(session, version),
        "provenance": version.provenance,
        "human_review_policy": rubric.human_review_policy,
        "rubric": rubric_projection(session, rubric),
        "audio": audio,
        "ai_proposal": version.ai_proposal,
        "human_approved_values": version.human_approved_values,
        "confidence": version.confidence_summary,
        "disagreement": version.disagreement_summary,
        "consistency": version.consistency_summary,
        "proposed_score": str(version.proposed_score)
        if version.proposed_score is not None
        else None,
        "approved_score": str(version.approved_score)
        if version.approved_score is not None
        else None,
        "review": {
            "id": str(review.id),
            "status": review.status.value,
            "requested_by": str(review.requested_by) if review.requested_by else None,
            "decided_by": str(review.decided_by) if review.decided_by else None,
            "decision": review.decision,
            "reason": review.reason,
            "reviewed_checksum": review.reviewed_checksum,
        }
        if review
        else None,
        "criteria": [
            {
                "criterion_code": row.criterion_code,
                "proposed_level": row.proposed_level,
                "proposed_points": str(row.proposed_points),
                "approved_level": row.approved_level,
                "approved_points": str(row.approved_points)
                if row.approved_points is not None
                else None,
                "rationale": row.rationale,
                "evidence": row.evidence,
                "confidence": row.confidence.value if row.confidence else None,
                "uncertainties": row.uncertainties,
                "disagreement": row.disagreement,
            }
            for row in version.criterion_assessments
        ],
        "history": [
            {
                **assessment_projection(row),
                "id": str(row.id),
                "assessment_version_id": str(row.id),
                "version_number": row.version_number,
                "created_at": row.created_at.isoformat(),
                "supersedes_version_id": str(row.supersedes_version_id)
                if row.supersedes_version_id
                else None,
                "assessed_by": row.provenance.get("assessed_by"),
                "reviewed_by": row.provenance.get("reviewed_by"),
            }
            for row in sorted(assessment.versions, key=lambda value: value.version_number)
        ],
    }


@router.get("/{organisation_id}/speaking-reviews")
def list_speaking_reviews(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal)
    _assert_reviewer(session, context, organisation_id)
    assessments = session.scalars(
        select(SubjectiveAssessment)
        .where(
            SubjectiveAssessment.organisation_id == organisation_id,
            SubjectiveAssessment.candidate_user_id != context.user_id,
        )
        .options(selectinload(SubjectiveAssessment.versions))
        .order_by(SubjectiveAssessment.created_at.desc())
    ).all()
    result = []
    for assessment in assessments:
        current = SubjectiveAssessmentService.current_version(assessment)
        if current is None or current.candidate_media_asset_id is None:
            continue
        result.append(
            {
                "assessment_id": str(assessment.id),
                "assessment_version_id": str(current.id),
                "attempt_id": str(assessment.attempt_id),
                "status": current.status.value,
                "assessment_origin": current.assessment_origin,
                "checksum": current.checksum,
                "review_required": current.status.value
                in {"AI_PROPOSED", "REVIEW_REQUIRED", "IN_REVIEW"},
                "transcript_available": current.speech_transcription_version_id is not None,
                "disagreement": current.disagreement_summary,
            }
        )
    return result


@router.get("/{organisation_id}/speaking-reviews/{assessment_id}")
def get_speaking_review(
    organisation_id: uuid.UUID,
    assessment_id: uuid.UUID,
    version_id: Optional[uuid.UUID] = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    _assert_reviewer(session, context, organisation_id)
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
        raise HTTPException(status_code=404, detail="speaking assessment not found")
    try:
        SubjectiveAssessmentService.assert_reviewer_access(context, assessment)
    except RubricError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    current = SubjectiveAssessmentService.current_version(assessment)
    selected = (
        next((row for row in assessment.versions if row.id == version_id), None)
        if version_id
        else current
    )
    if selected is None:
        raise HTTPException(status_code=404, detail="speaking assessment version not found")
    if selected.assessment_origin == "HUMAN":
        try:
            return speaking_detail(
                session, assessment, selected, is_current=selected is current, context=context
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        executed = _executed_detail(session, context, assessment, selected, current)
        if executed is not None:
            return executed
    except AuthorizationError as exc:
        raise HTTPException(
            status_code=403, detail="institution Speaking permission required"
        ) from exc
    except SpeakingAssessmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    current = selected
    if current is None or current.candidate_media_asset_id is None:
        raise HTTPException(status_code=404, detail="speaking assessment not found")
    return {
        "assessment_id": str(assessment.id),
        "assessment_version_id": str(current.id),
        "status": current.status.value,
        "original_audio_primary_evidence": True,
        "derived_transcription": current.speech_transcription_version_id is not None,
        "candidate_media_asset_id": str(current.candidate_media_asset_id),
        "audio_sha256": current.audio_sha256,
        "speech_transcription_version_id": str(current.speech_transcription_version_id)
        if current.speech_transcription_version_id
        else None,
        "transcript_checksum": current.transcript_checksum,
        "evidence_modes": current.evidence_mode_summary,
        "ai_proposal": current.ai_proposal,
        "human_approved_values": current.human_approved_values,
        "confidence": current.confidence_summary,
        "disagreement": current.disagreement_summary,
        "consistency": current.consistency_summary,
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
                "confidence": item.confidence.value if item.confidence else None,
                "uncertainties": item.uncertainties,
                "disagreement": item.disagreement,
            }
            for item in current.criterion_assessments
        ],
    }


@router.post("/{organisation_id}/speaking-reviews/{version_id}/propose")
def propose_speaking_review(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: dict[str, Any],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    _assert_reviewer(session, context, organisation_id)
    try:
        version = SpeakingAssessmentService.propose(
            session,
            context,
            version_id,
            lambda _attempt: payload["proposal"],
            ai_run_id=uuid.UUID(str(payload["ai_run_id"])),
        )
        session.commit()
        return {"id": str(version.id), "status": version.status.value, "checksum": version.checksum}
    except (SpeakingAssessmentError, KeyError, ValueError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
