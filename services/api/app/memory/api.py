from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import TenantContext
from app.db import get_db_session
from app.memory.governance import GovernedMemoryService
from app.models import LongTermMemory
from app.models.enums import (
    MemoryCategory,
    MemoryLifecycleStatus,
    MemoryOriginType,
    MemoryScopeType,
)

router = APIRouter(prefix="/api/v1/platform/memory", tags=["memory"])
service = GovernedMemoryService()


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="authorisation context required"
        )
    return principal.tenant_context


class MemoryCreate(BaseModel):
    code: str = Field(min_length=2, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    category: MemoryCategory
    scope_type: MemoryScopeType
    organisation_id: Optional[uuid.UUID] = None
    description: Optional[str] = Field(default=None, max_length=1000)


class VersionCreate(BaseModel):
    version_label: str = Field(min_length=1, max_length=80)
    content: dict[str, Any]
    origin_type: MemoryOriginType = MemoryOriginType.HUMAN_AUTHORED
    effective_from: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    review_after: Optional[datetime] = None
    proposed_by_ai_run_id: Optional[uuid.UUID] = None


class Transition(BaseModel):
    checksum: Optional[str] = None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_memory(
    payload: MemoryCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    try:
        memory = service.create_memory(session, _context(principal), **payload.model_dump())
        session.commit()
        return {"id": str(memory.id), "code": memory.code}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{memory_id}/versions", status_code=status.HTTP_201_CREATED)
def create_version(
    memory_id: uuid.UUID,
    payload: VersionCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    memory = session.get(LongTermMemory, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="memory not found")
    try:
        version = service.create_version(
            session, _context(principal), memory, **payload.model_dump()
        )
        session.commit()
        return {"id": str(version.id), "status": version.status.value, "checksum": version.checksum}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/versions/{version_id}/approve")
def approve(
    version_id: uuid.UUID,
    payload: Transition = Body(default=Transition()),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    from app.models import LongTermMemoryVersion

    version = session.get(LongTermMemoryVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    try:
        service.transition(
            session,
            _context(principal),
            version,
            MemoryLifecycleStatus.APPROVED,
            checksum=payload.checksum,
        )
        session.commit()
        return {"id": str(version.id), "status": version.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/versions/{version_id}/activate")
def activate(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, str]:
    from app.models import LongTermMemoryVersion
    from app.models.enums import MemoryLifecycleStatus

    version = session.get(LongTermMemoryVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    try:
        service.transition(session, _context(principal), version, MemoryLifecycleStatus.ACTIVE)
        session.commit()
        return {"id": str(version.id), "status": version.status.value}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/retrieve")
def retrieve(
    category: Optional[MemoryCategory] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, object]]:
    versions = service.retrieve(session, _context(principal), category=category, limit=limit)
    return [
        {
            "id": str(version.id),
            "memory_id": str(version.memory_id),
            "version": version.version_label,
            "content": version.content,
            "checksum": version.checksum,
            "provenance": version.origin_type.value,
        }
        for version in versions
    ]
