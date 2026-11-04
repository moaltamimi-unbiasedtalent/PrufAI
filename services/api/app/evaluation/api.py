"""Authenticated evaluation governance; summary routes never return private cases."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.control_center.api import platform_context
from app.db import get_db_session
from app.evaluation import service
from app.evaluation.schemas import (
    ChecksumRequest,
    DatasetApprovalRequest,
    DatasetVersionInput,
    QualityReviewRequest,
    RunRequest,
)
from app.models import EvaluationDataset

router = APIRouter(prefix="/api/v1/platform/evaluation", tags=["evaluation"])
summary_router = APIRouter(
    prefix="/api/v1/platform/control-center/evaluation", tags=["control-center"]
)


def evaluation_context(
    request: Request, principal: Principal = Depends(get_principal)
) -> TenantContext:
    if principal.tenant_context is None or principal.tenant_context.organisation_id is not None:
        raise HTTPException(status_code=403, detail="EVALUATION_PLATFORM_CONTEXT_REQUIRED")
    if "X-PrufAI-Organisation-ID" in request.headers:
        raise HTTPException(status_code=403, detail="EVALUATION_PLATFORM_CONTEXT_REQUIRED")
    return principal.tenant_context


def _call(session: Session, operation: Callable[[], Any], *, commit: bool = False) -> Any:
    try:
        result = operation()
        if commit:
            session.commit()
        return result
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.EvaluationConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="EVALUATION_STATE_CONFLICT") from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail="EVALUATION_STORAGE_UNAVAILABLE") from exc


@router.post("/datasets", status_code=201)
def create_dataset(
    payload: DatasetVersionInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: service.version_summary(
            session, service.create_dataset_version(session, context, payload)
        ),
        commit=True,
    )


@router.post("/datasets/{dataset_id}/versions", status_code=201)
def create_version(
    dataset_id: uuid.UUID,
    payload: DatasetVersionInput,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: service.version_summary(
            session, service.create_dataset_version(session, context, payload, dataset_id)
        ),
        commit=True,
    )


@router.get("/dataset-versions/{version_id}")
def dataset_version(
    version_id: uuid.UUID,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    def read() -> dict[str, Any]:
        service.require(session, context, service.READ)
        return service.version_summary(session, service._version(session, version_id))

    return _call(session, read)


@router.get("/dataset-versions/{version_id}/cases")
def dataset_cases(
    version_id: uuid.UUID,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    def read() -> dict[str, Any]:
        service.require(session, context, service.READ, service.REVIEW)
        row = service._version(session, version_id)
        # Phase1 original synthetic content only; this is never a candidate-data export.
        if row.provenance.get("origin") != "ORIGINAL_SYNTHETIC":
            raise AuthorizationError("EVALUATION_PRIVATE_SOURCE_ACCESS_UNCONFIGURED")
        if any(case.get("execution_job_id") for case in row.cases):
            raise AuthorizationError("EVALUATION_EXECUTION_EVIDENCE_EXPORT_FORBIDDEN")
        dataset = session.get(EvaluationDataset, row.dataset_id)
        assert dataset is not None
        if dataset.organisation_id is not None:
            tenant = build_tenant_context(session, context.user_id, dataset.organisation_id)
            permission = (
                "speech_transcription.read" if row.skill == "TRANSCRIPTION" else "subjective.review"
            )
            if tenant.membership_id is None or permission not in tenant.permissions:
                raise AuthorizationError("EVALUATION_PRIVATE_SOURCE_ACCESS_FORBIDDEN")
        return {
            "dataset_version_id": str(row.id),
            "checksum": row.checksum,
            "created_by": str(row.created_by),
            "approved_by": str(row.approved_by) if row.approved_by else None,
            "provenance": row.provenance,
            "cases": row.cases,
        }

    return _call(session, read)


@router.post("/dataset-versions/{version_id}/submit")
def submit(
    version_id: uuid.UUID,
    payload: ChecksumRequest,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: service.version_summary(
            session,
            service.transition_dataset(
                session, context, version_id, "submit", payload.expected_checksum
            ),
        ),
        commit=True,
    )


@router.post("/dataset-versions/{version_id}/approve")
def approve(
    version_id: uuid.UUID,
    payload: DatasetApprovalRequest,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: service.version_summary(
            session,
            service.transition_dataset(
                session,
                context,
                version_id,
                "approve",
                payload.expected_checksum,
                payload.provenance_review_reference,
            ),
        ),
        commit=True,
    )


@router.post("/dataset-versions/{version_id}/publish")
def publish(
    version_id: uuid.UUID,
    payload: ChecksumRequest,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: service.version_summary(
            session,
            service.transition_dataset(
                session, context, version_id, "publish", payload.expected_checksum
            ),
        ),
        commit=True,
    )


@router.post("/runs", status_code=201)
def run(
    payload: RunRequest,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(
        session,
        lambda: service.run_detail(
            session, context, service.execute_run(session, context, payload).id
        ),
        commit=True,
    )


@router.get("/runs/compare")
def compare(
    left_id: uuid.UUID,
    right_id: uuid.UUID,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(session, lambda: service.compare_runs(session, context, left_id, right_id))


@router.get("/runs/{run_id}")
def detail(
    run_id: uuid.UUID,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(session, lambda: service.run_detail(session, context, run_id))


@router.post("/runs/{run_id}/reviews", status_code=201)
def review(
    run_id: uuid.UUID,
    payload: QualityReviewRequest,
    context: TenantContext = Depends(evaluation_context),
    session: Session = Depends(get_db_session),
) -> Any:
    def apply() -> dict[str, Any]:
        result = service.review_run(
            session,
            context,
            run_id,
            payload.expected_run_checksum,
            payload.decision,
            payload.review_reference,
        )
        return {
            "id": str(result.id),
            "run_id": str(result.run_id),
            "decision": result.decision,
            "production_activation": False,
        }

    return _call(session, apply, commit=True)


@summary_router.get("")
def metadata(
    kind: Literal["datasets", "runs"] = "datasets",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(session, lambda: service.list_metadata(session, context, kind, offset, limit))


@summary_router.get("/runs/{run_id}")
def metadata_detail(
    run_id: uuid.UUID,
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> Any:
    return _call(session, lambda: service.run_detail(session, context, run_id))
