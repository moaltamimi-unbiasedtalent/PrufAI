"""Governed regression commands and safe, authenticated Control Centre reads."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.control_center.api import platform_context
from app.db import get_db_session
from app.evaluation import regression_registry as registry
from app.evaluation import regression_review as review
from app.evaluation import regression_thresholds as thresholds
from app.evaluation import service as foundation
from app.evaluation.api import _call, evaluation_context
from app.evaluation.regression_comparison import (
    RegressionComparisonInput,
    execute_regression_comparison,
)
from app.models.evaluation import REGRESSION_ADAPTER, EvaluationDatasetVersion, EvaluationRun

router = APIRouter(prefix="/api/v1/platform/evaluation", tags=["evaluation-regression"])
summary_router = APIRouter(
    prefix="/api/v1/platform/control-center/evaluation", tags=["control-center"]
)
Kind = Literal["regression-cases", "regression-suites"]
ORIGINS = {
    "regression-cases": "DYNAMIC_REGRESSION_CASE",
    "regression-suites": "DYNAMIC_REGRESSION_SUITE",
}


@router.post("/regression-cases", status_code=201)
def promote_case(
    payload: registry.PromotionRequest,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: registry.regression_summary(
            session, registry.promote_case(session, context, payload), context
        ),
        commit=True,
    )


@router.post("/regression-suites", status_code=201)
@router.post("/regression-suites/{dataset_id}/versions", status_code=201)
def create_suite(
    payload: registry.SuiteVersionRequest,
    dataset_id: uuid.UUID | None = None,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: registry.regression_summary(
            session, registry.create_suite(session, context, payload, dataset_id), context
        ),
        commit=True,
    )


@router.post("/regression-versions/{version_id}/{action}")
def transition_version(
    version_id: uuid.UUID,
    action: Literal["submit", "approve", "reject", "publish"],
    payload: registry.RegistryTransitionRequest,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: registry.regression_summary(
            session,
            registry.transition_regression_version(session, context, version_id, action, payload),
            context,
        ),
        commit=True,
    )


@router.post("/regression-comparisons", status_code=201)
def compare(
    payload: RegressionComparisonInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: review.comparison_detail(
            session, context, execute_regression_comparison(session, context, payload)
        ),
        commit=True,
    )


@router.post("/regression-comparisons/{run_id}/review", status_code=201)
def review_comparison(
    run_id: uuid.UUID,
    payload: review.ComparisonReviewInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: review.review_summary(review.record_review(session, context, run_id, payload)),
        commit=True,
    )


@router.post("/threshold-definitions", status_code=201)
def propose_threshold(
    payload: thresholds.ThresholdDefinitionInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: thresholds.threshold_summary(
            thresholds.propose_threshold(session, context, payload)
        ),
        commit=True,
    )


@router.post("/threshold-definitions/{identifier}/approve")
def approve_threshold(
    identifier: uuid.UUID,
    payload: thresholds.ThresholdApprovalInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: thresholds.threshold_summary(
            thresholds.approve_threshold(session, context, identifier, payload)
        ),
        commit=True,
    )


@router.get("/threshold-definitions/{identifier}/workbench")
def threshold_workbench(
    identifier: uuid.UUID,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(session, lambda: thresholds.threshold_workbench(session, context, identifier))


@summary_router.get("/regression-comparisons/{run_id}")
def comparison(
    run_id: uuid.UUID,
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: review.comparison_detail(session, context, review._regression_run(session, run_id)),
    )


@summary_router.get("/{kind}")
def versions(
    kind: Kind,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> Any:
    def read() -> dict[str, Any]:
        trusted = foundation.require(session, context, foundation.READ)
        origin = EvaluationDatasetVersion.provenance["origin"].as_string() == ORIGINS[kind]
        rows = session.scalars(
            select(EvaluationDatasetVersion)
            .where(origin)
            .order_by(EvaluationDatasetVersion.created_at.desc(), EvaluationDatasetVersion.id)
            .offset(offset)
            .limit(limit)
        )
        total = session.scalar(
            select(func.count()).select_from(EvaluationDatasetVersion).where(origin)
        )
        return {
            "items": [registry.regression_summary(session, row, trusted) for row in rows],
            "total": total or 0,
            "offset": offset,
            "limit": limit,
        }

    return _call(session, read)


@summary_router.get("/{kind}/{version_id}")
def version(
    kind: Kind,
    version_id: uuid.UUID,
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> Any:
    def read() -> dict[str, Any]:
        trusted = foundation.require(session, context, foundation.READ)
        row = foundation._version(session, version_id)
        if row.provenance.get("origin") != ORIGINS[kind]:
            raise ValueError("EVALUATION_REGRESSION_VERSION_NOT_FOUND")
        summary = registry.regression_summary(session, row, trusted)
        if kind == "regression-suites":
            runs = session.scalars(
                select(EvaluationRun)
                .where(
                    EvaluationRun.dataset_version_id == row.id,
                    EvaluationRun.configuration["execution_adapter"].as_string()
                    == REGRESSION_ADAPTER,
                    EvaluationRun.status.in_(("COMPLETED", "FAILED")),
                )
                .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id)
                .limit(10)
            )
            summary["recent_comparisons"] = [foundation.run_summary(session, run) for run in runs]
        return summary

    return _call(session, read)
