from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import TenantContext
from app.db import get_db_session
from app.exams.service import ExamStudioService
from app.models import Exam, ExamVersion
from app.models.enums import AssessmentClassification, ExamVersionStatus

router = APIRouter(prefix="/api/v1/platform", tags=["exam-studio"])


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="authorisation context required")
    return principal.tenant_context


class ExamCreate(BaseModel):
    code: str = Field(min_length=2, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    scope_type: str = Field(pattern="^(PLATFORM_SHARED|ORGANISATION_PRIVATE)$")
    organisation_id: Optional[uuid.UUID] = None
    description: Optional[str] = None


class VersionCreate(BaseModel):
    version_label: str = Field(min_length=1, max_length=80)
    standard_pack_version_id: uuid.UUID
    classification: AssessmentClassification = AssessmentClassification.PRACTICE
    supersedes_version_id: Optional[uuid.UUID] = None


class AssignmentCreate(BaseModel):
    item_version_id: uuid.UUID
    slot_index: int = Field(ge=0)


class AIProposal(BaseModel):
    task_id: uuid.UUID
    item_version_id: uuid.UUID
    slot_index: int = Field(ge=0)
    ai_run_id: Optional[uuid.UUID] = None
    ai_step_id: Optional[uuid.UUID] = None


def _version_payload(version: ExamVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "exam_id": str(version.exam_id),
        "version_label": version.version_label,
        "status": version.status.value,
        "standard_pack_version_id": str(version.standard_pack_version_id),
        "classification": version.classification.value,
        "checksum": version.checksum,
    }


@router.get("/exams")
def list_exams(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal)
    from app.authorization import AuthorizationService

    AuthorizationService.require_permission(context, "exam.read")
    query = select(Exam).order_by(Exam.created_at.desc()).offset(offset).limit(limit)
    if not context.platform_scope:
        query = query.where(
            (Exam.scope_type == "PLATFORM_SHARED")
            | (Exam.organisation_id == context.organisation_id)
        )
    return [
        {
            "id": str(exam.id),
            "code": exam.code,
            "name": exam.name,
            "scope_type": exam.scope_type,
            "organisation_id": str(exam.organisation_id) if exam.organisation_id else None,
        }
        for exam in session.scalars(query)
    ]


@router.post("/exams", status_code=status.HTTP_201_CREATED)
def create_exam(
    payload: ExamCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    try:
        exam = ExamStudioService.create_exam(session, _context(principal), **payload.model_dump())
        session.commit()
        return {"id": str(exam.id), "code": exam.code}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/exams/{exam_id}/versions", status_code=status.HTTP_201_CREATED)
def create_version(
    exam_id: uuid.UUID,
    payload: VersionCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    exam = session.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="exam not found")
    try:
        version = ExamStudioService.create_version(
            session, _context(principal), exam, **payload.model_dump()
        )
        session.commit()
        return _version_payload(version)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/exam-versions/{version_id}")
def get_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    ExamStudioService._require_visible(_context(principal), version.exam)
    return _version_payload(version)


@router.post("/exam-versions/{version_id}/materialize-blueprint")
def materialize(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    try:
        version = ExamStudioService.materialize_blueprint(session, _context(principal), version)
        session.commit()
        return _version_payload(version)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/exam-versions/{version_id}/eligible-items")
def eligible_items(
    version_id: uuid.UUID,
    task_spec_id: Optional[uuid.UUID] = None,
    limit: int = Query(50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    ExamStudioService._require_visible(_context(principal), version.exam)
    from app.question_bank.service import QuestionBankService

    return [
        {
            "id": str(item.id),
            "version_label": item.version_label,
            "checksum": item.checksum,
            "task_type": item.task_type.value,
            "response_mode": item.response_mode.value,
        }
        for item in QuestionBankService.eligible_bank(
            session,
            _context(principal),
            standard_pack_version_id=version.standard_pack_version_id,
            task_spec_id=task_spec_id,
            limit=limit,
        )
    ]


@router.post(
    "/exam-versions/{version_id}/tasks/{task_id}/items", status_code=status.HTTP_201_CREATED
)
def assign_item(
    version_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: AssignmentCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    try:
        binding = ExamStudioService.assign_item(
            session,
            _context(principal),
            version,
            task_id=task_id,
            item_version_id=payload.item_version_id,
            slot_index=payload.slot_index,
        )
        session.commit()
        return {
            "id": str(binding.id),
            "item_version_id": str(binding.question_bank_item_version_id),
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/exam-versions/{version_id}/item-bindings/{binding_id}")
def remove_item(
    version_id: uuid.UUID,
    binding_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    try:
        ExamStudioService.remove_item(session, _context(principal), version, binding_id)
        session.commit()
        return {"status": "removed"}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/exam-versions/{version_id}/validate")
def validate(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    result = ExamStudioService.validate(session, _context(principal), version)
    return {
        "status": result.status,
        "checksum": result.checksum,
        "issues": [issue.__dict__ for issue in result.issues],
    }


@router.get("/exam-versions/{version_id}/assembly-gaps")
def assembly_gaps(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    ExamStudioService._require_visible(_context(principal), version.exam)
    return ExamStudioService.assembly_gaps(session, version)


@router.post("/exam-versions/{version_id}/ai-proposal")
def ai_proposal(
    version_id: uuid.UUID,
    payload: list[AIProposal],
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    try:
        proposals = [item.model_dump() for item in payload]
        ai_run_id = proposals[0].get("ai_run_id") if proposals else None
        ai_step_id = proposals[0].get("ai_step_id") if proposals else None
        result = ExamStudioService.propose_ai_assembly(
            session,
            _context(principal),
            version,
            proposals,
            ai_run_id=ai_run_id,
            ai_step_id=ai_step_id,
        )
        session.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/exam-versions/{version_id}/request-review")
def request_review(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    try:
        review = ExamStudioService.request_review(session, _context(principal), version)
        session.commit()
        return {
            "id": str(review.id),
            "status": review.status.value,
            "checksum": review.reviewed_checksum or "",
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _transition(
    version_id: uuid.UUID, target: ExamVersionStatus, principal: Principal, session: Session
) -> dict[str, Any]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    try:
        version = ExamStudioService.transition(session, _context(principal), version, target)
        session.commit()
        return _version_payload(version)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/exam-versions/{version_id}/approve")
def approve(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _transition(version_id, ExamVersionStatus.APPROVED, principal, session)


@router.post("/exam-versions/{version_id}/publish")
def publish(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _transition(version_id, ExamVersionStatus.PUBLISHED, principal, session)


@router.post("/exam-versions/{version_id}/activate")
def activate(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _transition(version_id, ExamVersionStatus.ACTIVE, principal, session)


@router.post("/exam-versions/{version_id}/retire")
def retire(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _transition(version_id, ExamVersionStatus.RETIRED, principal, session)


@router.get("/exam-versions/{version_id}/manifest/admin")
def admin_manifest(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    return ExamStudioService.admin_manifest(session, _context(principal), version)


@router.get("/exam-versions/{version_id}/manifest/delivery-preview")
def delivery_manifest(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    version = session.get(ExamVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    return ExamStudioService.delivery_manifest(session, _context(principal), version)


@router.get("/exam-versions/{version_id}/diff/{other_id}")
def diff(
    version_id: uuid.UUID,
    other_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    version, other = session.get(ExamVersion, version_id), session.get(ExamVersion, other_id)
    if version is None or other is None:
        raise HTTPException(status_code=404, detail="exam version not found")
    return ExamStudioService.diff(session, _context(principal), version, other)
