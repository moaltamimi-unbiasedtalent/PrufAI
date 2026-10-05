from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, TenantContext
from app.db import get_db_session
from app.models import StatementOfResults
from app.result_release.service import ResultReleaseService

router = APIRouter(prefix="/api/v1/organisations", tags=["result-release"])
candidate_router = APIRouter(prefix="/api/v1/candidate", tags=["candidate-release"])


class OperationRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=120)


class AuthorizationRequest(BaseModel):
    assessment_scope: str = Field(min_length=1, max_length=120)
    provider_scope: str = Field(min_length=1, max_length=120)
    evidence_reference: str = Field(min_length=1, max_length=240)
    effective_from: datetime


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/{organisation_id}/result-versions/{version_id}/release")
def release_result(
    organisation_id: uuid.UUID,
    version_id: uuid.UUID,
    request: OperationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        release = ResultReleaseService.release(
            session, context, version_id, operation_id=request.operation_id
        )
        session.commit()
        return {"id": str(release.id), "status": release.status.value, "checksum": release.checksum}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _handle(exc) from exc


@router.get("/{organisation_id}/releases")
def list_releases(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    return [
        {
            "id": str(release.id),
            "candidate_user_id": str(release.candidate_user_id),
            "result_version_id": str(release.result_version_id),
            "classification": release.classification,
            "status": release.status.value,
            "released_at": release.released_at,
        }
        for release in ResultReleaseService.organisation_releases(session, context)
    ]


@router.post("/{organisation_id}/releases/{release_id}/statement")
def issue_statement(
    organisation_id: uuid.UUID,
    release_id: uuid.UUID,
    request: OperationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        statement = ResultReleaseService.statement(
            session, context, release_id, operation_id=request.operation_id
        )
        session.commit()
        return {
            "id": str(statement.id),
            "status": statement.status.value,
            "checksum": statement.checksum,
            "payload": statement.payload,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _handle(exc) from exc


@router.post("/{organisation_id}/certification-authorizations")
def create_authorization(
    organisation_id: uuid.UUID,
    request: AuthorizationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        authorization = ResultReleaseService.authorize(
            session,
            context,
            assessment_scope=request.assessment_scope,
            provider_scope=request.provider_scope,
            evidence_reference=request.evidence_reference,
            effective_from=request.effective_from,
        )
        session.commit()
        return {
            "id": str(authorization.id),
            "status": authorization.status.value,
            "checksum": authorization.checksum,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _handle(exc) from exc


@router.post("/{organisation_id}/releases/{release_id}/certificate")
def issue_certificate(
    organisation_id: uuid.UUID,
    release_id: uuid.UUID,
    authorization_id: uuid.UUID,
    request: OperationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        certificate = ResultReleaseService.certificate(
            session, context, release_id, authorization_id, operation_id=request.operation_id
        )
        session.commit()
        return {
            "id": str(certificate.id),
            "status": certificate.status.value,
            "serial": certificate.serial,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _handle(exc) from exc


@candidate_router.get("/releases")
def candidate_releases(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    return [
        {
            "id": str(release.id),
            "result_version_id": str(release.result_version_id),
            "classification": release.classification,
            "released_at": release.released_at,
            "status": release.status.value,
        }
        for release in ResultReleaseService.candidate_releases(session, _context(principal))
    ]


@candidate_router.get("/releases/{release_id}/statement")
def candidate_statement(
    release_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    release = ResultReleaseService._owned_release(session, context, release_id)
    if release.candidate_user_id != context.user_id:
        raise HTTPException(status_code=404, detail="statement not found")
    statement = session.scalar(
        select(StatementOfResults).where(StatementOfResults.release_id == release.id)
    )
    if statement is None:
        raise HTTPException(status_code=404, detail="statement not found")
    return {
        "id": str(statement.id),
        "classification": statement.classification,
        "payload": statement.payload,
    }
