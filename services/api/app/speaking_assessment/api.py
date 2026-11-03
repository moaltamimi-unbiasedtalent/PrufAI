from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationService, TenantContext
from app.db import get_db_session
from app.models import SubjectiveAssessment, SubjectiveAssessmentVersion
from app.speaking_assessment.service import SpeakingAssessmentError, SpeakingAssessmentService
from app.writing_assessment.service import SubjectiveAssessmentService

router = APIRouter(prefix="/api/v1/organisations", tags=["speaking-assessment"])


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


def _assert_reviewer(context: TenantContext, organisation_id: uuid.UUID) -> None:
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    AuthorizationService.require_permission(context, "subjective.read", organisation_id)


@router.get("/{organisation_id}/speaking-reviews")
def list_speaking_reviews(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal)
    _assert_reviewer(context, organisation_id)
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
                "review_required": current.status.value in {"AI_PROPOSED", "REVIEW_REQUIRED"},
                "transcript_available": current.speech_transcription_version_id is not None,
                "disagreement": current.disagreement_summary,
            }
        )
    return result


@router.get("/{organisation_id}/speaking-reviews/{assessment_id}")
def get_speaking_review(
    organisation_id: uuid.UUID,
    assessment_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    _assert_reviewer(context, organisation_id)
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
    SubjectiveAssessmentService.assert_reviewer_access(context, assessment)
    current = SubjectiveAssessmentService.current_version(assessment)
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
    _assert_reviewer(context, organisation_id)
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
