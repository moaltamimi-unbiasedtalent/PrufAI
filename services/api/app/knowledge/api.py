from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService
from app.db import get_db_session
from app.knowledge.storage import InMemoryObjectStorage, StorageError
from app.models import (
    KnowledgeAuthority,
    KnowledgeDocumentPage,
    KnowledgeDocumentVersion,
    KnowledgeLegalMetadata,
    KnowledgeSource,
)
from app.models.enums import (
    AssessmentFamily,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeScope,
)
from app.services.knowledge import KnowledgeRegistryService

router = APIRouter(prefix="/api/v1/platform/standards", tags=["standards"])
_storage = InMemoryObjectStorage()
_registry = KnowledgeRegistryService(storage=_storage)


class AuthorityCreateRequest(BaseModel):
    code: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    authority_type: KnowledgeAuthorityType
    jurisdiction: Optional[str] = None
    website_url: Optional[str] = None


class SourceCreateRequest(BaseModel):
    authority_id: uuid.UUID
    source_code: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=1, max_length=300)
    document_type: KnowledgeDocumentType
    assessment_family: AssessmentFamily
    scope: KnowledgeScope = KnowledgeScope.PLATFORM_SHARED
    language: Optional[str] = None
    provider_code: Optional[str] = None
    exam_family: Optional[str] = None
    cefr_level: Optional[str] = None
    skill: Optional[str] = None
    canonical_url: Optional[str] = None
    notes: Optional[str] = None


class VersionCreateRequest(BaseModel):
    version_label: str = Field(min_length=1, max_length=120)
    publication_date: Optional[datetime] = None
    effective_from: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    source_url: Optional[str] = None
    supersedes_document_version_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None


class FileRegisterRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)
    declared_mime_type: Optional[str] = None


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def _platform(principal: Principal):
    if principal.tenant_context is None or not principal.tenant_context.platform_scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="platform authority required"
        )
    return principal.tenant_context


@router.get("/authorities")
def list_authorities(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, object]]:
    _platform(principal)
    AuthorizationService.require_permission(_platform(principal), "standards.authority.read")
    return [
        {
            "id": str(item.id),
            "code": item.code,
            "name": item.name,
            "authority_type": item.authority_type.value,
        }
        for item in session.scalars(
            select(KnowledgeAuthority).order_by(KnowledgeAuthority.code).offset(offset).limit(limit)
        )
    ]


@router.post("/authorities", status_code=status.HTTP_201_CREATED)
def create_authority(
    payload: AuthorityCreateRequest = Body(..., embed=False),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _platform(principal)
    try:
        authority = _registry.create_authority(
            session, _platform(principal), **payload.model_dump()
        )
    except (ValueError, AuthorizationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"id": str(authority.id), "code": authority.code, "name": authority.name}


@router.post("/sources", status_code=status.HTTP_201_CREATED)
def create_source(
    payload: SourceCreateRequest = Body(..., embed=False),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _platform(principal)
    try:
        source = _registry.create_source(session, _platform(principal), **payload.model_dump())
    except (ValueError, AuthorizationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"id": str(source.id), "source_code": source.source_code, "title": source.title}


@router.get("/sources")
def list_sources(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, object]]:
    _platform(principal)
    AuthorizationService.require_permission(_platform(principal), "standards.source.read")
    return [
        {
            "id": str(item.id),
            "source_code": item.source_code,
            "title": item.title,
            "assessment_family": item.assessment_family.value,
            "exam_family": item.exam_family,
            "cefr_level": item.cefr_level,
        }
        for item in session.scalars(
            select(KnowledgeSource)
            .order_by(KnowledgeSource.source_code)
            .offset(offset)
            .limit(limit)
        )
    ]


@router.get("/authorities/{authority_id}")
def get_authority(
    authority_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _platform(principal)
    AuthorizationService.require_permission(_platform(principal), "standards.authority.read")
    authority = session.get(KnowledgeAuthority, authority_id)
    if authority is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="authority not found")
    return {
        "id": str(authority.id),
        "code": authority.code,
        "name": authority.name,
        "authority_type": authority.authority_type.value,
    }


@router.get("/sources/{source_id}")
def get_source(
    source_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _platform(principal)
    AuthorizationService.require_permission(_platform(principal), "standards.source.read")
    source = session.get(KnowledgeSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="source not found")
    return {
        "id": str(source.id),
        "source_code": source.source_code,
        "title": source.title,
        "assessment_family": source.assessment_family.value,
        "provider_code": source.provider_code,
        "exam_family": source.exam_family,
        "language": source.language,
        "cefr_level": source.cefr_level,
        "skill": source.skill,
        "document_type": source.document_type.value,
        "scope": source.scope.value,
    }


@router.get("/sources/{source_id}/versions")
def list_source_versions(
    source_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, object]]:
    _platform(principal)
    AuthorizationService.require_permission(_platform(principal), "standards.source.read")
    return [
        {
            "id": str(version.id),
            "version_label": version.version_label,
            "status": version.status.value,
            "effective_from": version.effective_from,
            "effective_until": version.effective_until,
            "supersedes_document_version_id": str(version.supersedes_document_version_id)
            if version.supersedes_document_version_id
            else None,
        }
        for version in session.scalars(
            select(KnowledgeDocumentVersion)
            .where(KnowledgeDocumentVersion.source_id == source_id)
            .order_by(
                KnowledgeDocumentVersion.effective_from, KnowledgeDocumentVersion.version_label
            )
            .offset(offset)
            .limit(limit)
        )
    ]


@router.get("/versions/{version_id}")
def get_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _platform(principal)
    AuthorizationService.require_permission(_platform(principal), "standards.document.read")
    version = session.get(KnowledgeDocumentVersion, version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="version not found")
    legal = session.scalar(
        select(KnowledgeLegalMetadata).where(
            KnowledgeLegalMetadata.document_version_id == version.id
        )
    )
    return {
        "id": str(version.id),
        "version_label": version.version_label,
        "status": version.status.value,
        "sha256": version.sha256,
        "effective_from": version.effective_from,
        "effective_until": version.effective_until,
        "source_url": version.source_url,
        "legal_review_status": legal.legal_review_status.value if legal else "NOT_REVIEWED",
    }


@router.get("/versions/{version_id}/pages")
def list_version_pages(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, object]]:
    _platform(principal)
    AuthorizationService.require_permission(_platform(principal), "standards.document.read")
    return [
        {
            "id": str(page.id),
            "page_number": page.page_number,
            "character_count": page.character_count,
            "parser_name": page.parser_name,
            "parser_version": page.parser_version,
        }
        for page in session.scalars(
            select(KnowledgeDocumentPage)
            .where(KnowledgeDocumentPage.document_version_id == version_id)
            .order_by(KnowledgeDocumentPage.page_number)
            .offset(offset)
            .limit(limit)
        )
    ]


@router.get("/versions/{version_id}/provenance")
def version_provenance(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _platform(principal)
    AuthorizationService.require_permission(_platform(principal), "standards.document.read")
    version = session.get(KnowledgeDocumentVersion, version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="version not found")
    source = session.get(KnowledgeSource, version.source_id)
    authority = session.get(KnowledgeAuthority, source.authority_id) if source else None
    return {
        "authority": authority.code if authority else None,
        "source_code": source.source_code if source else None,
        "document_version_id": str(version.id),
        "version_label": version.version_label,
        "sha256": version.sha256,
        "status": version.status.value,
        "effective_from": version.effective_from,
        "effective_until": version.effective_until,
    }


@router.post("/sources/{source_id}/versions", status_code=status.HTTP_201_CREATED)
def create_version(
    source_id: uuid.UUID,
    payload: VersionCreateRequest = Body(..., embed=False),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _platform(principal)
    try:
        version = _registry.create_version(
            session, _platform(principal), source_id=source_id, **payload.model_dump()
        )
    except (ValueError, AuthorizationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "id": str(version.id),
        "version_label": version.version_label,
        "status": version.status.value,
    }


@router.post("/versions/{version_id}/file")
def register_file(
    version_id: uuid.UUID,
    payload: FileRegisterRequest = Body(..., embed=False),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _platform(principal)
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
        version = _registry.register_file(
            session,
            _platform(principal),
            version_id=version_id,
            filename=payload.filename,
            content=content,
            declared_mime_type=payload.declared_mime_type,
        )
    except (ValueError, AuthorizationError, StorageError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"id": str(version.id), "sha256": version.sha256, "mime_type": version.mime_type}


@router.post("/versions/{version_id}/ingest")
def ingest_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _platform(principal)
    try:
        job = _registry.ingest(session, _platform(principal), version_id=version_id)
    except (ValueError, AuthorizationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"id": str(job.id), "status": job.status.value, "pages_detected": job.pages_detected}


@router.post("/versions/{version_id}/approve")
def approve_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _platform(principal)
    try:
        version = _registry.approve(session, _platform(principal), version_id=version_id)
    except (ValueError, AuthorizationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "id": str(version.id),
        "status": version.status.value,
        "approved_at": version.approved_at,
    }


@router.post("/versions/{version_id}/reject")
def reject_version(
    version_id: uuid.UUID,
    payload: RejectRequest = Body(..., embed=False),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _platform(principal)
    try:
        version = _registry.reject(
            session, _platform(principal), version_id=version_id, reason=payload.reason
        )
    except (ValueError, AuthorizationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"id": str(version.id), "status": version.status.value}
