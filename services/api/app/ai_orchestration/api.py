from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_orchestration.service import AIOrchestrationService
from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.db import get_db_session
from app.models import (
    AIModelProfile,
    AIRun,
    AIWorkflowVersion,
    HumanReviewRequest,
)
from app.models.enums import HumanReviewStatus

router = APIRouter(prefix="/api/v1/platform/ai", tags=["ai-orchestration"])


def _require(principal: Principal, permission: str) -> TenantContext:
    context = principal.tenant_context
    if context is None or not context.platform_scope:
        raise HTTPException(status_code=403, detail="platform authority required")
    try:
        AuthorizationService.require_permission(context, permission)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return context


class ModelRequest(BaseModel):
    code: str = Field(min_length=3, max_length=160)
    name: str
    provider: str
    model_name: str
    purpose: str
    configuration: dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)


class ReviewDecision(BaseModel):
    decision: HumanReviewStatus
    checksum: Optional[str] = None
    reason: Optional[str] = None


class ReviewRequest(BaseModel):
    entity_type: str = Field(min_length=1, max_length=120)
    entity_id: uuid.UUID
    checksum: Optional[str] = None


@router.get("/models")
def list_models(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db_session)
) -> list[dict[str, Any]]:
    _require(principal, "ai.read")
    return [
        {
            "id": str(item.id),
            "code": item.code,
            "provider": item.provider,
            "model_name": item.model_name,
            "status": item.status.value,
        }
        for item in session.scalars(select(AIModelProfile).order_by(AIModelProfile.code))
    ]


@router.post("/models", status_code=status.HTTP_201_CREATED)
def create_model(
    payload: ModelRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    context = _require(principal, "ai.manage")
    try:
        item = AIOrchestrationService.create_model(session, context=context, **payload.model_dump())
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": str(item.id), "code": item.code, "status": item.status.value}


@router.post("/workflow-versions/{version_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_workflow(
    version_id: uuid.UUID,
    payload: RunRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    context = _require(principal, "ai.run")
    workflow = session.get(AIWorkflowVersion, version_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow version not found")
    try:
        run = AIOrchestrationService.start_run(
            session,
            workflow,
            request_id=payload.request_id,
            initiated_by_user_id=principal.user.id,
            context=context,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": str(run.id), "status": run.status.value}


@router.get("/runs/{run_id}")
def get_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    _require(principal, "ai.read")
    run = session.get(AIRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="AI run not found")
    return {"id": str(run.id), "status": run.status.value, "request_id": run.request_id}


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    context = _require(principal, "ai.run")
    run = session.get(AIRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="AI run not found")
    try:
        AIOrchestrationService.cancel_run(session, run, context=context)
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": str(run.id), "status": run.status.value}


@router.post("/runs/{run_id}/review", status_code=status.HTTP_201_CREATED)
def request_review(
    run_id: uuid.UUID,
    payload: ReviewRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    context = _require(principal, "ai.review")
    run = session.get(AIRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="AI run not found")
    try:
        item = AIOrchestrationService.request_review(
            session,
            run,
            requested_by=principal.user.id,
            context=context,
            **payload.model_dump(),
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": str(item.id), "status": item.status.value}


@router.get("/reviews")
def list_reviews(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db_session)
) -> list[dict[str, str]]:
    _require(principal, "ai.review")
    return [
        {"id": str(item.id), "status": item.status.value, "review_type": item.review_type}
        for item in session.scalars(
            select(HumanReviewRequest).order_by(HumanReviewRequest.created_at.desc())
        )
    ]


@router.post("/reviews/{review_id}/decision")
def decide_review(
    review_id: uuid.UUID,
    payload: ReviewDecision,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    context = _require(principal, "ai.review")
    review = session.get(HumanReviewRequest, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    try:
        item = AIOrchestrationService.decide_review(
            session,
            review,
            decision=payload.decision,
            actor_id=principal.user.id,
            checksum=payload.checksum,
            reason=payload.reason,
            context=context,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": str(item.id), "status": item.status.value}


def _decision(
    review_id: uuid.UUID,
    decision: HumanReviewStatus,
    principal: Principal,
    session: Session,
) -> dict[str, str]:
    context = _require(principal, "ai.review")
    review = session.get(HumanReviewRequest, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    try:
        item = AIOrchestrationService.decide_review(
            session, review, decision=decision, actor_id=principal.user.id, context=context
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": str(item.id), "status": item.status.value}


@router.post("/reviews/{review_id}/approve")
def approve_review(
    review_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    return _decision(review_id, HumanReviewStatus.APPROVED, principal, session)


@router.post("/reviews/{review_id}/reject")
def reject_review(
    review_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    return _decision(review_id, HumanReviewStatus.REJECTED, principal, session)


@router.post("/reviews/{review_id}/request-changes")
def request_review_changes(
    review_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    return _decision(review_id, HumanReviewStatus.CHANGES_REQUESTED, principal, session)
