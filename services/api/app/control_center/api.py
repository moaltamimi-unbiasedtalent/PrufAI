from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, TenantContext
from app.config import Settings, get_settings
from app.control_center.service import ControlCenterService
from app.db import get_db_session

router = APIRouter(prefix="/api/v1/platform/control-center", tags=["control-center"])


def platform_context(
    request: Request, principal: Principal = Depends(get_principal)
) -> TenantContext:
    context = principal.tenant_context
    try:
        if context is None:
            raise AuthorizationError("platform Control Centre authority required")
        ControlCenterService.require(context)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if "X-PrufAI-Organisation-ID" in request.headers:
        raise HTTPException(
            status_code=400, detail="use a metadata filter, not tenant context headers"
        )
    return context


def _call(operation: Any, *args: Any) -> dict[str, Any]:
    try:
        result: dict[str, Any] = operation(*args)
        return result
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/overview")
def overview(
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _call(ControlCenterService.overview, session, context, settings)


@router.get("/organisations")
def organisations(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(ControlCenterService.organisations, session, context, offset, limit)


@router.get("/standards")
def standards(
    kind: Literal["versions", "packs", "authorities", "bindings"] = "versions",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(ControlCenterService.standards, session, context, kind, offset, limit)


@router.get("/source-registry")
def source_registry(
    kind: Literal["sources", "documents", "ingestions"] = "sources",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(ControlCenterService.sources, session, context, kind, offset, limit)


@router.get("/question-bank")
def question_bank(
    organisation_id: Optional[uuid.UUID] = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(
        ControlCenterService.scoped_metadata,
        session,
        context,
        "question-bank",
        offset,
        limit,
        organisation_id,
    )


@router.get("/exams")
def exams(
    organisation_id: Optional[uuid.UUID] = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(
        ControlCenterService.scoped_metadata,
        session,
        context,
        "exams",
        offset,
        limit,
        organisation_id,
    )


@router.get("/audit")
def audit(
    organisation_id: Optional[uuid.UUID] = None,
    event_type: Optional[str] = Query(
        default=None, min_length=1, max_length=160, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]*$"
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(
        ControlCenterService.audit, session, context, offset, limit, organisation_id, event_type
    )


@router.get("/ai-operations")
def ai_operations(
    kind: Literal[
        "models",
        "workflows",
        "runs",
        "reviews",
        "checkpoints",
        "transcriptions",
        "writing-proposals",
    ] = "models",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(ControlCenterService.ai_operations, session, context, kind, offset, limit)


@router.get("/system")
def system(
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    return _call(ControlCenterService.system, session, context, settings)


@router.get("/certification")
def certification(
    kind: Literal["authorizations", "certificates"] = "authorizations",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    context: TenantContext = Depends(platform_context),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _call(ControlCenterService.certification, session, context, kind, offset, limit)
