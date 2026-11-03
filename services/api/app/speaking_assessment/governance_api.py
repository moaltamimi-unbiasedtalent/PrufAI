from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import TenantContext
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
from app.models.enums import CandidateAttemptStatus, QuestionBankSkill, RubricVersionStatus
from app.speaking_assessment.governance import HumanSpeakingService
from app.speaking_audio.api import asset_projection
from app.writing_assessment.governance import review_for
from app.writing_assessment.governance_api import (
    DecisionRequest,
    InitializeRequest,
    OperationRequest,
    ProposalRequest,
    _context,
    _failure,
    assessment_projection,
    review_projection,
    rubric_projection,
)
from app.writing_assessment.service import SubjectiveAssessmentService

human_speaking_router = APIRouter()


class SpeakingInitializeRequest(InitializeRequest):
    candidate_response_revision_id: uuid.UUID


class SuccessorRequest(OperationRequest):
    reason: str = Field(min_length=1, max_length=2000)


def speaking_detail(
    session: Session,
    assessment: SubjectiveAssessment,
    version: SubjectiveAssessmentVersion,
    *,
    is_current: bool,
    context: TenantContext,
) -> dict[str, Any]:
    rubric, revision = HumanSpeakingService._pins(session, version)
    attempt = session.get(CandidateExamAttempt, version.attempt_id)
    assert attempt is not None  # _pins has validated the exact persisted attempt.
    asset = HumanSpeakingService._audio(
        session,
        attempt,
        revision,
        version.exam_version_item_binding_id,
    )
    audio = asset_projection(session, asset)
    audio["candidate_response_revision_id"] = str(revision.id)
    current = SubjectiveAssessmentService.current_version(assessment)
    task = session.get(StandardPackTaskSpecification, rubric.task_spec_id)
    item = session.get(QuestionBankItemVersion, version.question_bank_item_version_id)
    review = review_for(
        session,
        "SUBJECTIVE_ASSESSMENT_VERSION",
        version.id,
        review_type=HumanSpeakingService.REVIEW_TYPE,
    )
    return {
        **assessment_projection(version),
        "assessment_id": str(assessment.id),
        "assessment_version_id": str(version.id),
        "attempt_id": str(version.attempt_id),
        "standard_pack_version_id": str(version.standard_pack_version_id),
        "task_spec_id": str(rubric.task_spec_id),
        "task_label": task.name if task else "Speaking task",
        "task_prompt": str(item.content.get("prompt", ""))
        if item and "question_bank.read" in context.permissions
        else "",
        "task_prompt_available": bool(item and "question_bank.read" in context.permissions),
        "exam_version_item_binding_id": str(version.exam_version_item_binding_id),
        "question_bank_item_version_id": str(version.question_bank_item_version_id),
        "candidate_response_revision_id": str(revision.id),
        "response_checksum": version.response_checksum,
        "rubric_checksum": version.rubric_checksum,
        "current_version_id": str(current.id) if current else None,
        "is_current_version": is_current,
        "version_number": version.version_number,
        "assessment_mode": "HUMAN_ONLY",
        "evidence_mode": "AUDIO",
        "original_audio_primary_evidence": True,
        "derived_transcription": False,
        "candidate_media_asset_id": str(asset.id),
        "audio_sha256": version.audio_sha256,
        "speech_transcription_version_id": None,
        "transcript_checksum": None,
        "evidence_modes": version.evidence_mode_summary,
        "ai_proposal": {},
        "confidence": {},
        "disagreement": {},
        "consistency": {},
        "human_approved_values": version.human_approved_values,
        "proposed_score": str(version.proposed_score)
        if version.proposed_score is not None
        else None,
        "audio": audio,
        "rubric": rubric_projection(session, rubric),
        "provenance": version.provenance,
        "assessed_by": version.provenance.get("assessed_by"),
        "assessor_id": version.provenance.get("assessed_by"),
        "reviewer_id": version.provenance.get("reviewed_by"),
        "review": {
            **(
                review_projection(
                    session,
                    "SUBJECTIVE_ASSESSMENT_VERSION",
                    version.id,
                    review_type=HumanSpeakingService.REVIEW_TYPE,
                )
                or {}
            ),
            "decision": review.decision if review else None,
            "reason": review.reason if review else None,
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
                "confidence": None,
                "uncertainties": [],
                "disagreement": {},
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
                "decision": row.provenance.get("decision"),
            }
            for row in sorted(assessment.versions, key=lambda value: value.version_number)
        ],
    }


@human_speaking_router.get("/{organisation_id}/speaking-review-options")
def speaking_review_options(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "subjective.read")
    try:
        require_active_tenant_permission(session, context, "subjective.assess")
        require_active_tenant_permission(session, context, "subjective.read")
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
                    or item.skill is not QuestionBankSkill.SPEAKING
                    or not response.revisions
                ):
                    continue
                existing = session.scalar(
                    select(SubjectiveAssessment).where(
                        SubjectiveAssessment.attempt_id == attempt.id,
                        SubjectiveAssessment.exam_version_item_binding_id == binding.id,
                    )
                )
                if existing is not None:
                    continue
                revision = max(response.revisions, key=lambda row: row.revision_number)
                try:
                    asset = HumanSpeakingService._audio(session, attempt, revision, binding.id)
                    rubric = session.get(RubricVersion, uuid.UUID(str(item.rubric_reference)))
                    if rubric is None or rubric.rubric.organisation_id != organisation_id:
                        continue
                    HumanSpeakingService.assert_human_mode(rubric)
                except (ValueError, KeyError, TypeError):
                    continue
                task = session.get(
                    StandardPackTaskSpecification, binding.task.standard_pack_task_spec_id
                )
                submissions.append(
                    {
                        "attempt_id": str(attempt.id),
                        "exam_version_item_binding_id": str(binding.id),
                        "candidate_response_revision_id": str(revision.id),
                        "response_checksum": revision.payload_checksum,
                        "question_bank_item_version_id": str(item.id),
                        "rubric_version_id": str(rubric.id),
                        "standard_pack_version_id": str(rubric.standard_pack_version_id),
                        "task_spec_id": str(binding.task.standard_pack_task_spec_id),
                        "task_label": task.name if task else "Speaking task",
                        "candidate_media_asset_id": str(asset.id),
                        "audio_sha256": asset.sha256,
                        "duration_ms": asset.duration_ms,
                    }
                )
        rubrics = session.scalars(
            select(RubricVersion)
            .join(AssessmentRubric)
            .where(
                AssessmentRubric.organisation_id == organisation_id,
                RubricVersion.skill == QuestionBankSkill.SPEAKING,
                RubricVersion.status.in_(
                    [RubricVersionStatus.PUBLISHED, RubricVersionStatus.ACTIVE]
                ),
            )
            .limit(100)
        ).all()
        return {
            "submissions": submissions[:100],
            "rubrics": [rubric_projection(session, row) for row in rubrics],
        }
    except Exception as exc:  # noqa: BLE001
        raise _failure(exc) from exc


@human_speaking_router.post("/{organisation_id}/subjective-assessments/human-speaking")
def initialize_speaking(
    organisation_id: uuid.UUID,
    payload: SpeakingInitializeRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "subjective.assess")
    try:
        version = HumanSpeakingService.initialize(
            session,
            context,
            operation_id=payload.operation_id,
            attempt_id=payload.attempt_id,
            binding_id=payload.exam_version_item_binding_id,
            response_revision_id=payload.candidate_response_revision_id,
            rubric_version_id=payload.rubric_version_id,
            response_checksum=payload.response_checksum,
            rubric_checksum=payload.rubric_checksum,
        )
        session.commit()
        return assessment_projection(version)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _failure(exc) from exc


@human_speaking_router.post("/{organisation_id}/speaking-reviews/{version_id}/human-proposal")
def propose_speaking(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: ProposalRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "subjective.assess")
    try:
        version = HumanSpeakingService.propose(
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


@human_speaking_router.post("/{organisation_id}/speaking-reviews/{version_id}/human-approve")
def approve_speaking(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: OperationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "subjective.review")
    try:
        version = HumanSpeakingService.approve(
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


@human_speaking_router.post("/{organisation_id}/speaking-reviews/{version_id}/human-decision")
def decide_speaking(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: DecisionRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "subjective.review")
    try:
        version = HumanSpeakingService.decide(
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


@human_speaking_router.post("/{organisation_id}/speaking-reviews/{version_id}/human-successor")
def successor_speaking(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: SuccessorRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal, organisation_id, "subjective.assess")
    try:
        version = HumanSpeakingService.successor(
            session,
            context,
            version_id,
            operation_id=payload.operation_id,
            expected_checksum=payload.expected_checksum,
            reason=payload.reason,
        )
        session.commit()
        return assessment_projection(version)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _failure(exc) from exc
