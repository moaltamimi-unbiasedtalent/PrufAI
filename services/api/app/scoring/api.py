from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.db import get_db_session
from app.models import AssessmentResult, ResultVersion, ScoringRun
from app.result_release.service import ResultReleaseService
from app.scoring.service import ScoringService

scoring_router = APIRouter(prefix="/api/v1/organisations", tags=["scoring-results"])
candidate_result_router = APIRouter(prefix="/api/v1/candidate", tags=["candidate-results"])


class ReleaseRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=120)
    supersedes_release_id: Optional[uuid.UUID] = None


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, (AuthorizationError, PermissionError)):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


def _admin_version(
    session: Session, version: ResultVersion, result: AssessmentResult
) -> dict[str, Any]:
    return {
        "result_id": str(result.id),
        "result_version_id": str(version.id),
        "version_number": version.version_number,
        "scoring_run_id": str(version.scoring_run_id),
        "assessment_components": ScoringService.assessment_components(
            session.get(ScoringRun, version.scoring_run_id)
        ),
        "review": ScoringService.review_projection(session, version),
        "attempt_id": str(result.attempt_id),
        "status": version.status.value,
        "raw_awarded_points": str(version.raw_awarded_points)
        if version.raw_awarded_points is not None
        else None,
        "raw_maximum_points": str(version.raw_maximum_points)
        if version.raw_maximum_points is not None
        else None,
        "outcome": version.outcome.value if version.outcome else None,
        "incomplete_reason": version.incomplete_reason,
        "checksum": version.checksum,
        "response_set_checksum": version.response_set_checksum,
        "scoring_run_checksum": version.scoring_run_checksum,
        "rule_provenance": version.rule_provenance,
        "created_at": version.created_at,
        "released_at": version.released_at,
    }


@scoring_router.post("/{organisation_id}/exam-attempts/{attempt_id}/score")
def score_attempt(
    organisation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        run = ScoringService.score(session, context, attempt_id)
        session.commit()
        return {
            "id": str(run.id),
            "status": run.status.value,
            "response_set_checksum": run.response_set_checksum,
            "checksum": run.checksum,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@scoring_router.get("/{organisation_id}/scoring-runs/{run_id}")
def get_scoring_run(
    organisation_id: uuid.UUID,
    run_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    AuthorizationService.require_permission(context, "result.read", organisation_id)
    run = session.get(ScoringRun, run_id)
    if run is None or run.organisation_id != organisation_id:
        raise HTTPException(status_code=404, detail="scoring run not found")
    return {
        "id": str(run.id),
        "attempt_id": str(run.attempt_id),
        "status": run.status.value,
        "response_set_checksum": run.response_set_checksum,
        "scoring_engine": f"{run.scoring_engine_name}@{run.scoring_engine_version}",
        "assessment_components": ScoringService.assessment_components(run),
        "checksum": run.checksum,
    }


@scoring_router.get("/{organisation_id}/results/{result_id}")
def get_result(
    organisation_id: uuid.UUID,
    result_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    AuthorizationService.require_permission(context, "result.read", organisation_id)
    result = session.get(AssessmentResult, result_id)
    if result is None or result.organisation_id != organisation_id:
        raise HTTPException(status_code=404, detail="result not found")
    current = max(
        (row for row in result.versions if row.status.value != "SUPERSEDED"),
        key=lambda row: row.version_number,
        default=None,
    )
    if current is None:
        raise HTTPException(status_code=404, detail="result version not found")
    return _admin_version(session, current, result)


@scoring_router.get("/{organisation_id}/results/{result_id}/versions")
def list_result_versions(
    organisation_id: uuid.UUID,
    result_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal)
    AuthorizationService.require_permission(context, "result.read", organisation_id)
    result = session.get(AssessmentResult, result_id)
    if result is None or result.organisation_id != organisation_id:
        raise HTTPException(status_code=404, detail="result not found")
    return [_admin_version(session, version, result) for version in result.versions]


@scoring_router.post("/{organisation_id}/result-versions/{version_id}/request-review")
def request_review(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        review = ScoringService.request_review(session, context, version_id)
        session.commit()
        return {
            "id": str(review.id),
            "status": review.status.value,
            "reviewed_checksum": review.reviewed_checksum,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@scoring_router.post("/{organisation_id}/result-versions/{version_id}/approve")
def approve_result(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        version = ScoringService.approve_review(session, context, version_id)
        session.commit()
        return {"id": str(version.id), "status": version.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@scoring_router.post("/{organisation_id}/result-versions/{version_id}/release")
def release_result(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    request: ReleaseRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        release = ResultReleaseService.release(
            session,
            context,
            version_id,
            operation_id=request.operation_id,
            supersedes_release_id=request.supersedes_release_id,
        )
        version = session.get(ResultVersion, release.result_version_id)
        if version is None:
            raise ValueError("released result version is unavailable")
        session.commit()
        return {
            "id": str(release.result_version_id),
            "release_id": str(release.id),
            "status": version.status.value,
            "release_status": release.status.value,
            "effective_status": ResultReleaseService.effective_status(session, release),
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@scoring_router.post("/{organisation_id}/result-versions/{version_id}/reject")
def reject_result(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        version = ScoringService.reject_review(session, context, version_id)
        session.commit()
        return {"id": str(version.id), "status": version.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _error(exc) from exc


@scoring_router.get("/{organisation_id}/result-versions/{version_id}/diff/{other_id}")
def diff_result(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    other_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        return ScoringService.diff(session, context, version_id, other_id)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@candidate_result_router.get("/results")
def candidate_results(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    return ScoringService.candidate_results(session, _context(principal))


@candidate_result_router.get("/results/{result_id}")
def candidate_result(
    result_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    rows = [
        row
        for row in ScoringService.candidate_results(session, _context(principal))
        if row["result_id"] == str(result_id)
    ]
    if not rows:
        raise HTTPException(status_code=404, detail="result not found")
    return rows[0]
