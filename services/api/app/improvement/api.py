"""Platform-only commands and safe projections for evaluated draft candidates."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.control_center.api import platform_context
from app.db import get_db_session
from app.evaluation.api import _call as call_evaluation
from app.evaluation.api import evaluation_context
from app.evaluation.prompt_policy import PromptPolicyBaselineInput, execute_prompt_policy_baseline
from app.evaluation.service import READ, run_summary
from app.feedback.api import _call
from app.feedback.common import require_platform
from app.improvement import candidates, readiness
from app.models.feedback import GovernedChangeCandidate

router = APIRouter(prefix="/api/v1/platform/feedback/change-candidates", tags=["feedback"])
evaluation_router = APIRouter(prefix="/api/v1/platform/evaluation", tags=["evaluation"])
summary_router = APIRouter(
    prefix="/api/v1/platform/control-center/feedback/change-candidates", tags=["control-center"]
)


@evaluation_router.post("/prompt-policy-runs", status_code=201)
def execute_baseline(
    payload: PromptPolicyBaselineInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return call_evaluation(
        session,
        lambda: run_summary(session, execute_prompt_policy_baseline(session, context, payload)),
        commit=True,
    )


@router.post("", status_code=201)
def create(
    payload: candidates.ChangeCandidateInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(
        session,
        lambda: readiness.candidate_detail(
            session, context, candidates.create_change_candidate(session, context, payload)
        ),
        commit=True,
    )


@router.post("/{identifier}/evaluate")
def evaluate(
    identifier: uuid.UUID,
    payload: readiness.EvaluateCandidateInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(
        session,
        lambda: readiness.receipt_summary(
            readiness.evaluate_candidate(session, context, identifier, payload)
        ),
        commit=True,
    )


@router.post("/{identifier}/review")
def review(
    identifier: uuid.UUID,
    payload: readiness.ReviewCandidateInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(
        session,
        lambda: readiness.receipt_summary(
            readiness.review_candidate(session, context, identifier, payload)
        ),
        commit=True,
    )


@summary_router.get("")
def list_candidates(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    def read() -> dict[str, Any]:
        trusted = require_platform(session, context, READ)
        scope = GovernedChangeCandidate.organisation_id.is_(None)
        rows = session.scalars(
            select(GovernedChangeCandidate)
            .where(scope)
            .order_by(GovernedChangeCandidate.created_at.desc(), GovernedChangeCandidate.id)
            .offset(offset)
            .limit(limit)
        )
        total = session.scalar(
            select(func.count()).select_from(GovernedChangeCandidate).where(scope)
        )
        return {
            "items": [readiness.candidate_summary(session, trusted, row) for row in rows],
            "total": total or 0,
            "offset": offset,
            "limit": limit,
        }

    return _call(session, read)


@summary_router.get("/{identifier}")
def detail(
    identifier: uuid.UUID,
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    def read() -> dict[str, Any]:
        trusted = require_platform(session, context, READ)
        row = readiness._get(session, identifier)
        if row is None:
            raise HTTPException(status_code=404, detail="IMPROVEMENT_CANDIDATE_NOT_FOUND")
        return readiness.candidate_detail(session, trusted, row)

    return _call(session, read)
