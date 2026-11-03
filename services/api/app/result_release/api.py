from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.db import get_db_session
from app.models import (
    CertificateRecord,
    ResultRelease,
    ResultVersion,
    ScoringRun,
    StatementOfResults,
)
from app.result_release.service import ResultReleaseError, ResultReleaseService
from app.scoring.service import ScoringService

router = APIRouter(prefix="/api/v1/organisations", tags=["result-release"])
candidate_router = APIRouter(prefix="/api/v1/candidate", tags=["candidate-release"])


class OperationRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=120)


class AuthorizationRequest(BaseModel):
    assessment_scope: str = Field(min_length=1, max_length=120)
    provider_scope: str = Field(min_length=1, max_length=120)
    evidence_reference: str = Field(min_length=1, max_length=240)
    effective_from: datetime
    effective_until: Optional[datetime] = None
    standard_pack_version_id: uuid.UUID
    operation_id: str = Field(min_length=1, max_length=120)


class RevocationRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=240)
    operation_id: str = Field(min_length=1, max_length=120)


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, AuthorizationError):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


def _artifact(session: Session, release: ResultRelease, artifact: Any) -> dict[str, Any]:
    effective = ResultReleaseService.artifact_status(session, release, artifact)
    payload = {
        "id": str(artifact.id),
        "status": artifact.status.value,
        "effective_status": effective,
        "currently_valid": effective == "ISSUED",
        "checksum": artifact.checksum,
    }
    if isinstance(artifact, StatementOfResults):
        payload.update(
            {
                "classification": artifact.classification,
                "payload": artifact.payload,
                "issued_at": (
                    artifact.issued_at.astimezone(timezone.utc)
                    if artifact.issued_at.tzinfo
                    else artifact.issued_at.replace(tzinfo=timezone.utc)
                ).isoformat(),
            }
        )
    else:
        payload.update(
            {
                "serial": artifact.serial,
                "authorization_id": str(artifact.authorization_id),
                "authorization_version": artifact.authorization_version,
            }
        )
    return payload


def _payload(session: Session, release: ResultRelease, *, detail: bool = False) -> dict[str, Any]:
    result = {
        "id": str(release.id),
        "candidate_user_id": str(release.candidate_user_id),
        "attempt_id": str(release.attempt_id),
        "assessment_result_id": str(release.assessment_result_id),
        "result_version_id": str(release.result_version_id),
        "scoring_run_id": str(release.scoring_run_id),
        "standard_pack_version_id": str(release.standard_pack_version_id),
        "classification": release.classification,
        "status": release.status.value,
        "effective_status": ResultReleaseService.effective_status(session, release),
        "released_at": release.released_at,
        "released_by_user_id": str(release.released_by_user_id),
        "checksum": release.checksum,
        "result_checksum": release.metadata_json.get("result_checksum"),
        "supersedes_release_id": str(release.supersedes_release_id)
        if release.supersedes_release_id
        else None,
    }
    if detail:
        version = session.get(ResultVersion, release.result_version_id)
        result["result"] = (
            {
                "result_version_id": str(version.id),
                "raw_awarded_points": str(version.raw_awarded_points)
                if version.raw_awarded_points is not None
                else None,
                "raw_maximum_points": str(version.raw_maximum_points)
                if version.raw_maximum_points is not None
                else None,
                "outcome": version.outcome.value if version.outcome else None,
                "result_checksum": version.checksum,
                "assessment_components": ScoringService.assessment_components(
                    session.get(ScoringRun, release.scoring_run_id)
                ),
            }
            if version is not None
            else None
        )
        result["statements"] = [
            _artifact(session, release, row)
            for row in session.scalars(
                select(StatementOfResults).where(StatementOfResults.release_id == release.id)
            )
        ]
        result["certificates"] = [
            _artifact(session, release, row)
            for row in session.scalars(
                select(CertificateRecord).where(CertificateRecord.release_id == release.id)
            )
        ]
    return result


@router.get("/{organisation_id}/releases")
def list_releases(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        return [
            _payload(session, release)
            for release in ResultReleaseService.organisation_releases(session, context)
        ]
    except AuthorizationError as exc:
        raise _handle(exc) from exc


@router.get("/{organisation_id}/releases/{release_id}")
def release_detail(
    organisation_id: uuid.UUID,
    release_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        AuthorizationService.require_permission(context, "result.read", organisation_id)
        release = ResultReleaseService._owned_release(session, context, release_id)
        return _payload(session, release, detail=True)
    except AuthorizationError as exc:
        raise _handle(exc) from exc
    except ResultReleaseError as exc:
        raise HTTPException(status_code=404, detail="release not found") from exc


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
        return _artifact(
            session, ResultReleaseService._owned_release(session, context, release_id), statement
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _handle(exc) from exc


@router.post("/{organisation_id}/releases/{release_id}/revoke")
def revoke_release(
    organisation_id: uuid.UUID,
    release_id: uuid.UUID,
    request: RevocationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        release = ResultReleaseService.revoke_release(
            session, context, release_id, request.reason, operation_id=request.operation_id
        )
        session.commit()
        return {"id": str(release.id), "status": release.status.value}
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
            effective_until=request.effective_until,
            standard_pack_version_id=request.standard_pack_version_id,
            operation_id=request.operation_id,
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


@router.post("/{organisation_id}/certification-authorizations/{authorization_id}/revoke")
def revoke_authorization(
    organisation_id: uuid.UUID,
    authorization_id: uuid.UUID,
    request: RevocationRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        authorization = ResultReleaseService.revoke_authorization(
            session, context, authorization_id, request.reason, operation_id=request.operation_id
        )
        session.commit()
        return {"id": str(authorization.id), "status": authorization.status.value}
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
        return _artifact(
            session, ResultReleaseService._owned_release(session, context, release_id), certificate
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _handle(exc) from exc


@candidate_router.get("/releases")
def candidate_releases(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    return [
        _payload(session, release)
        for release in ResultReleaseService.candidate_releases(session, _context(principal))
    ]


def _candidate_release(
    session: Session, context: TenantContext, release_id: uuid.UUID
) -> ResultRelease:
    release = session.scalar(
        select(ResultRelease).where(
            ResultRelease.id == release_id,
            ResultRelease.organisation_id == context.organisation_id,
            ResultRelease.candidate_user_id == context.user_id,
        )
    )
    if release is None:
        raise HTTPException(status_code=404, detail="release not found")
    return ResultReleaseService._owned_release(session, context, release.id)


@candidate_router.get("/releases/{release_id}")
def candidate_release_detail(
    release_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _payload(
        session, _candidate_release(session, _context(principal), release_id), detail=True
    )


@candidate_router.get("/releases/{release_id}/statement")
def candidate_statement(
    release_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    release = _candidate_release(session, context, release_id)
    statement = session.scalar(
        select(StatementOfResults).where(StatementOfResults.release_id == release.id)
    )
    if statement is None:
        raise HTTPException(status_code=404, detail="statement not found")
    return _artifact(session, release, statement)
