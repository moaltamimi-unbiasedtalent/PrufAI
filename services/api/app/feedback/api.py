"""Authenticated feedback commands and scoped metadata reads."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.authorization.membership import require_active_tenant_permission
from app.control_center.api import platform_context
from app.db import get_db_session
from app.evaluation.api import evaluation_context
from app.evaluation.service import READ, EvaluationConflict
from app.feedback import intake, proposals
from app.feedback.common import FeedbackConflict, require_platform
from app.models.base import Base
from app.models.feedback import FeedbackPattern, GovernedFeedback, ImprovementProposal

router = APIRouter(prefix="/api/v1/platform/feedback", tags=["feedback"])
summary_router = APIRouter(
    prefix="/api/v1/platform/control-center/feedback", tags=["control-center"]
)
tenant_router = APIRouter(
    prefix="/api/v1/organisations/{organisation_id}/feedback", tags=["feedback"]
)

Kind = Literal["items", "patterns", "proposals"]
Snapshot = GovernedFeedback | FeedbackPattern | ImprovementProposal
_MODELS: dict[Kind, type[Snapshot]] = {
    "items": GovernedFeedback,
    "patterns": FeedbackPattern,
    "proposals": ImprovementProposal,
}


def _call(
    session: Session, callback: Callable[[], dict[str, Any]], *, commit: bool = False
) -> dict[str, Any]:
    try:
        result = callback()
        if commit:
            session.commit()
        return result
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (FeedbackConflict, EvaluationConflict) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="FEEDBACK_STATE_CONFLICT") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="FEEDBACK_STORAGE_UNAVAILABLE") from exc


def tenant_context(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
) -> TenantContext:
    context = principal.tenant_context
    if context is None or context.organisation_id != organisation_id or not context.membership_id:
        raise HTTPException(status_code=403, detail="FEEDBACK_INSTITUTION_CONTEXT_REQUIRED")
    return context


@router.post("/items", status_code=201)
def submit(
    payload: intake.FeedbackIntakeInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(session, lambda: intake.intake_feedback(session, context, payload), commit=True)


@tenant_router.post("", status_code=201)
def submit_institution(
    payload: intake.FeedbackIntakeInput,
    context: TenantContext = Depends(tenant_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(session, lambda: intake.intake_feedback(session, context, payload), commit=True)


@router.post("/items/{identifier}/decision")
def decide_feedback(
    identifier: uuid.UUID,
    payload: intake.FeedbackDecisionInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(
        session, lambda: intake.decide_feedback(session, context, identifier, payload), commit=True
    )


@tenant_router.post("/{identifier}/decision")
def decide_institution_feedback(
    identifier: uuid.UUID,
    payload: intake.FeedbackDecisionInput,
    context: TenantContext = Depends(tenant_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(
        session, lambda: intake.decide_feedback(session, context, identifier, payload), commit=True
    )


@router.post("/patterns", status_code=201)
def create_pattern(
    payload: proposals.PatternInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(session, lambda: proposals.create_pattern(session, context, payload), commit=True)


@router.post("/proposals", status_code=201)
def create_proposal(
    payload: proposals.ProposalInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(session, lambda: proposals.create_proposal(session, context, payload), commit=True)


@router.post("/proposals/{identifier}/decision")
def decide_proposal(
    identifier: uuid.UUID,
    payload: proposals.ProposalDecisionInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(
        session,
        lambda: proposals.decide_proposal(session, context, identifier, payload),
        commit=True,
    )


def _summary(session: Session, context: TenantContext, row: Base) -> dict[str, Any]:
    if isinstance(row, GovernedFeedback):
        return intake.feedback_summary(session, row, context)
    if isinstance(row, FeedbackPattern):
        return proposals.pattern_summary(session, context, row)
    assert isinstance(row, ImprovementProposal)
    return proposals.proposal_summary(session, context, row)


@summary_router.get("/{kind}")
def list_platform(
    kind: Kind,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    def read() -> dict[str, Any]:
        trusted = require_platform(session, context, READ)
        model = _MODELS[kind]
        scope = model.organisation_id.is_(None)
        rows = session.scalars(
            select(model)
            .where(scope)
            .order_by(model.created_at.desc(), model.id)
            .offset(offset)
            .limit(limit)
        )
        total = session.scalar(select(func.count()).select_from(model).where(scope)) or 0
        return {
            "items": [_summary(session, trusted, row) for row in rows],
            "offset": offset,
            "limit": limit,
            "total": total,
        }

    return _call(session, read)


@summary_router.get("/{kind}/{identifier}")
def platform_detail(
    kind: Kind,
    identifier: uuid.UUID,
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    def read() -> dict[str, Any]:
        trusted = require_platform(session, context, READ)
        model = _MODELS[kind]
        row = session.scalar(
            select(model).where(model.id == identifier, model.organisation_id.is_(None))
        )
        if row is None:
            raise HTTPException(status_code=404, detail="FEEDBACK_RECORD_NOT_FOUND")
        return _summary(session, trusted, row)

    return _call(session, read)


@tenant_router.get("")
def list_institution(
    source_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(tenant_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    def read() -> dict[str, Any]:
        trusted = build_tenant_context(session, context.user_id, context.organisation_id)
        permission = next(
            (
                key
                for key in (
                    "subjective.review",
                    "appeal.read",
                    "exam.review",
                    "question_bank.review",
                )
                if key in trusted.permissions
            ),
            None,
        )
        if permission is None:
            raise AuthorizationError("FEEDBACK_INSTITUTION_REVIEW_REQUIRED")
        require_active_tenant_permission(session, trusted, permission)
        scope = (
            GovernedFeedback.organisation_id == trusted.organisation_id,
            GovernedFeedback.source["source_id"].as_string() == str(source_id),
        )
        rows = session.scalars(
            select(GovernedFeedback)
            .where(*scope)
            .order_by(GovernedFeedback.created_at.desc(), GovernedFeedback.id)
            .offset(offset)
            .limit(limit)
        )
        total = session.scalar(select(func.count()).select_from(GovernedFeedback).where(*scope))
        return {
            "items": [intake.feedback_summary(session, row, trusted) for row in rows],
            "offset": offset,
            "limit": limit,
            "total": total or 0,
        }

    return _call(session, read)
