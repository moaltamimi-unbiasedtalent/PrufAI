from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import TenantContext
from app.db import get_db_session
from app.models import QuestionBankItem, QuestionBankItemCefrLevel, QuestionBankItemVersion
from app.models.enums import (
    AssessmentFamily,
    PackMediaRequirement,
    PackResponseMode,
    QuestionBankLifecycleStatus,
    QuestionBankOriginType,
    QuestionBankRightsStatus,
    QuestionBankScoringPolicy,
    QuestionBankSkill,
    QuestionBankTaskType,
)
from app.question_bank.service import (
    QuestionBankService,
    delivery_projection,
    validate_item_payload,
)

router = APIRouter(prefix="/api/v1/platform/question-bank", tags=["question-bank"])
service = QuestionBankService()


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="authorisation context required")
    return principal.tenant_context


class ItemCreate(BaseModel):
    code: str = Field(min_length=2, max_length=160)
    scope_type: str = Field(pattern="^(PLATFORM_SHARED|ORGANISATION_PRIVATE)$")
    organisation_id: Optional[uuid.UUID] = None


class VersionCreate(BaseModel):
    version_label: str = Field(min_length=1, max_length=80)
    assessment_family: AssessmentFamily
    task_type: QuestionBankTaskType
    response_mode: PackResponseMode
    content: dict[str, Any]
    language: Optional[str] = None
    exam_family: Optional[str] = None
    cefr_levels: list[str] = Field(default_factory=list, max_length=6)
    skill: Optional[QuestionBankSkill] = None
    media_requirement: PackMediaRequirement = PackMediaRequirement.NONE
    scoring_policy: QuestionBankScoringPolicy = QuestionBankScoringPolicy.OBJECTIVE_KEYED
    max_points: Optional[Decimal] = Field(default=None, ge=0)
    scoring_metadata: dict[str, Any] = Field(default_factory=dict)
    rights_status: QuestionBankRightsStatus = QuestionBankRightsStatus.UNKNOWN
    rights_holder: Optional[str] = Field(default=None, max_length=240)
    licence_reference: Optional[str] = Field(default=None, max_length=240)
    rubric_reference: Optional[str] = Field(default=None, max_length=240)
    origin_type: QuestionBankOriginType = QuestionBankOriginType.HUMAN_AUTHORED
    proposed_by_ai_run_id: Optional[uuid.UUID] = None
    proposed_by_ai_step_id: Optional[uuid.UUID] = None


class TransitionRequest(BaseModel):
    checksum: Optional[str] = None


@router.post("/items", status_code=status.HTTP_201_CREATED)
def create_item(
    payload: ItemCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    try:
        item = service.create_item(session, _context(principal), **payload.model_dump())
        session.commit()
        return {"id": str(item.id), "code": item.code}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/items/{item_id}/versions", status_code=status.HTTP_201_CREATED)
def create_version(
    item_id: uuid.UUID,
    payload: VersionCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    item = session.get(QuestionBankItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    try:
        version = service.create_version(session, _context(principal), item, **payload.model_dump())
        session.commit()
        return {"id": str(version.id), "status": version.status.value, "checksum": version.checksum}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/items")
def search_items(
    status_filter: Optional[QuestionBankLifecycleStatus] = Query(default=None, alias="status"),
    assessment_family: Optional[AssessmentFamily] = None,
    language: Optional[str] = None,
    cefr_level: Optional[str] = None,
    task_type: Optional[QuestionBankTaskType] = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    versions = service.search(
        session,
        _context(principal),
        status=status_filter,
        assessment_family=assessment_family,
        language=language,
        cefr_level=cefr_level,
        task_type=task_type,
        offset=offset,
        limit=limit,
    )
    return [
        {
            "id": str(version.id),
            "item_id": str(version.item_id),
            "version": version.version_label,
            "status": version.status.value,
            "assessment_family": version.assessment_family.value,
            "language": version.language,
            "checksum": version.checksum,
        }
        for version in versions
    ]


@router.post("/item-versions/{version_id}/validate")
def validate_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    version = session.get(QuestionBankItemVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    context = _context(principal)
    if version.item.organisation_id != context.organisation_id and not context.platform_scope:
        raise HTTPException(status_code=403, detail="question bank scope denied")
    result = validate_item_payload(
        assessment_family=version.assessment_family,
        exam_family=version.exam_family,
        language=version.language,
        cefr_levels=list(
            session.scalars(
                select(QuestionBankItemCefrLevel.cefr_level).where(
                    QuestionBankItemCefrLevel.item_version_id == version.id
                )
            )
        ),
        skill=version.skill,
        task_type=version.task_type,
        response_mode=version.response_mode,
        media_requirement=version.media_requirement,
        content=version.content,
        scoring_policy=version.scoring_policy,
        max_points=version.max_points,
        scoring_metadata=version.scoring_metadata,
        rights_status=version.rights_status,
        licence_reference=version.licence_reference,
        rubric_reference=version.rubric_reference,
    )
    return {
        "status": result.status,
        "checksum": result.checksum,
        "issues": [issue.__dict__ for issue in result.issues],
    }


@router.get("/item-versions/{version_id}/export")
def export_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    version = session.get(QuestionBankItemVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    context = _context(principal)
    if version.item.organisation_id != context.organisation_id and not context.platform_scope:
        raise HTTPException(status_code=403, detail="question bank scope denied")
    from app.authorization import AuthorizationService

    AuthorizationService.require_permission(
        context, "question_bank.export", version.item.organisation_id
    )
    return service.export_safe(version, cefr_levels=[], pack_binding_ids=[], evidence_ids=[])


@router.get("/item-versions/{version_id}/delivery")
def delivery_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    version = session.get(QuestionBankItemVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    context = _context(principal)
    if version.item.organisation_id != context.organisation_id and not context.platform_scope:
        raise HTTPException(status_code=403, detail="question bank scope denied")
    if not service.eligible_for_exam_studio(version):
        raise HTTPException(status_code=409, detail="item version is not eligible for delivery")
    return delivery_projection(version)


@router.post("/item-versions/{version_id}/approve")
def approve(
    version_id: uuid.UUID,
    payload: TransitionRequest = Body(default=TransitionRequest()),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    return _transition(
        version_id, QuestionBankLifecycleStatus.APPROVED, payload, principal, session
    )


@router.post("/item-versions/{version_id}/request-review")
def request_review(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    return _transition(
        version_id,
        QuestionBankLifecycleStatus.HUMAN_REVIEW,
        TransitionRequest(),
        principal,
        session,
    )


@router.post("/item-versions/{version_id}/publish")
def publish(
    version_id: uuid.UUID,
    payload: TransitionRequest = Body(default=TransitionRequest()),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    return _transition(
        version_id, QuestionBankLifecycleStatus.PUBLISHED, payload, principal, session
    )


def _transition(
    version_id: uuid.UUID,
    target: QuestionBankLifecycleStatus,
    payload: TransitionRequest,
    principal: Principal,
    session: Session,
) -> dict[str, str]:
    version = session.get(QuestionBankItemVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    result = validate_item_payload(
        assessment_family=version.assessment_family,
        exam_family=version.exam_family,
        language=version.language,
        cefr_levels=list(
            session.scalars(
                select(QuestionBankItemCefrLevel.cefr_level).where(
                    QuestionBankItemCefrLevel.item_version_id == version.id
                )
            )
        ),
        skill=version.skill,
        task_type=version.task_type,
        response_mode=version.response_mode,
        media_requirement=version.media_requirement,
        content=version.content,
        scoring_policy=version.scoring_policy,
        max_points=version.max_points,
        scoring_metadata=version.scoring_metadata,
        rights_status=version.rights_status,
        licence_reference=version.licence_reference,
        rubric_reference=version.rubric_reference,
    )
    try:
        service.transition(session, _context(principal), version, target, result, payload.checksum)
        session.commit()
        return {"id": str(version.id), "status": version.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
