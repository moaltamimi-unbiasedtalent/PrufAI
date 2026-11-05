"""Authenticated human reference governance and safe Control Centre metadata."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.authorization import TenantContext
from app.control_center.api import platform_context
from app.db import get_db_session
from app.evaluation import expert_service as service
from app.evaluation import service as foundation
from app.evaluation.api import _call, evaluation_context
from app.evaluation.expert_requests import (
    AdjudicationInput,
    ApprovalInput,
    CalibrationInput,
    JudgmentInput,
    QualificationInput,
    ReferenceDatasetInput,
)
from app.evaluation.expert_schemas import SourceInput
from app.models.expert_evaluation import EvaluationReference

router = APIRouter(prefix="/api/v1/platform/evaluation", tags=["evaluation-references"])
summary_router = APIRouter(
    prefix="/api/v1/platform/control-center/evaluation", tags=["control-center"]
)


def _decision(row: Any) -> dict[str, Any]:
    return {"id": str(row.id), "checksum": row.checksum, "created_at": row.created_at.isoformat()}


@router.post("/references", status_code=201)
def create(
    payload: SourceInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: service.reference_summary(
            session, service.create_reference(session, context, payload)
        ),
        commit=True,
    )


@router.post("/references/{identifier}/approve")
def approve(
    identifier: uuid.UUID,
    payload: ApprovalInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: service.reference_summary(
            session,
            service.approve_reference(
                session, context, identifier, payload.expected_checksum, payload.review_reference
            ),
        ),
        commit=True,
    )


@router.get("/references/{identifier}/workbench")
def workbench(
    identifier: uuid.UUID,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(session, lambda: service.workbench(session, context, identifier))


@router.post("/examiner-qualifications", status_code=201)
def qualification(
    payload: QualificationInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: _decision(service.create_qualification(session, context, payload)),
        commit=True,
    )


@router.post("/examiner-qualifications/{identifier}/approve")
def approve_qualification(
    identifier: uuid.UUID,
    payload: ApprovalInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: _decision(
            service.approve_qualification(
                session, context, identifier, payload.expected_checksum, payload.review_reference
            )
        ),
        commit=True,
    )


@router.post("/references/{identifier}/judgments", status_code=201)
def judgment(
    identifier: uuid.UUID,
    payload: JudgmentInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: _decision(service.submit_judgment(session, context, identifier, payload)),
        commit=True,
    )


@router.post("/references/{identifier}/adjudications", status_code=201)
def adjudication(
    identifier: uuid.UUID,
    payload: AdjudicationInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: {
            **_decision(row := service.adjudicate(session, context, identifier, payload)),
            "version_number": row.version_number,
        },
        commit=True,
    )


@router.post("/references/{identifier}/dataset-versions", status_code=201)
def derive(
    identifier: uuid.UUID,
    payload: ReferenceDatasetInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: foundation.version_summary(
            session, service.derive_dataset(session, context, identifier, payload)
        ),
        commit=True,
    )


@router.post("/calibrations", status_code=201)
def calibrate(
    payload: CalibrationInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    from app.evaluation.expert_comparison import calibrate as compute

    return _call(
        session,
        lambda: foundation.run_summary(session, compute(session, context, payload)),
        commit=True,
    )


@summary_router.get("/references")
def references(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> Any:
    def read() -> dict[str, Any]:
        foundation.require(session, context, foundation.READ)
        rows = session.scalars(
            select(EvaluationReference)
            .order_by(EvaluationReference.created_at.desc(), EvaluationReference.id)
            .offset(offset)
            .limit(limit)
        )
        return {
            "items": [service.reference_summary(session, row) for row in rows],
            "offset": offset,
            "limit": limit,
            "total": session.scalar(select(func.count()).select_from(EvaluationReference)) or 0,
        }

    return _call(session, read)
