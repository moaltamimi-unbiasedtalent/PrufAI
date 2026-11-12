from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.config import Settings, get_settings
from app.db import get_db_session
from app.knowledge.storage import (
    MAX_SOURCE_FILE_SIZE,
    S3CompatibleObjectStorage,
    StorageError,
    StorageIntegrityError,
    build_object_storage,
)
from app.models import (
    KnowledgeAuthority,
    KnowledgeDocumentPage,
    KnowledgeDocumentVersion,
    KnowledgeIngestionJob,
    KnowledgeLegalMetadata,
    KnowledgeSource,
)
from app.models.enums import (
    AssessmentFamily,
    KnowledgeAuthorityType,
    KnowledgeDocumentType,
    KnowledgeScope,
)
from app.operations.telemetry import Failure, Signal, record
from app.services.knowledge import KnowledgeRegistryService, SourceConflict

router = APIRouter(prefix="/api/v1/platform/standards", tags=["standards"])
_registry = KnowledgeRegistryService()
_SHA256 = r"^[a-f0-9]{64}$"


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
    model_config = ConfigDict(extra="forbid")

    version_label: str = Field(min_length=1, max_length=120)
    content_mode: Literal["METADATA_ONLY", "ORIGINAL_SYNTHETIC"] = "METADATA_ONLY"
    publication_date: Optional[datetime] = None
    retrieved_at: Optional[datetime] = None
    effective_from: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    source_url: Optional[str] = Field(default=None, max_length=500)
    supersedes_document_version_id: Optional[uuid.UUID] = None
    notes: Optional[str] = Field(default=None, max_length=4000)
    rights_holder: Optional[str] = Field(default=None, max_length=300)
    licence_type: Optional[str] = Field(default=None, max_length=120)


class FileRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=((MAX_SOURCE_FILE_SIZE + 2) // 3) * 4)
    declared_mime_type: Optional[str] = Field(default=None, max_length=120)


class RightsReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_metadata_checksum: str = Field(pattern=_SHA256)
    decision: Literal["METADATA_ONLY", "ALLOW_SYNTHETIC_INGESTION", "DENY", "WITHDRAW"]
    basis_reference: str = Field(min_length=1, max_length=240)
    rights_holder: str = Field(min_length=1, max_length=300)
    licence_type: str = Field(min_length=1, max_length=120)
    retention_policy: str = Field(min_length=1, max_length=240)
    expected_sha256: str | None = Field(default=None, pattern=_SHA256)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_sha256: str = Field(pattern=_SHA256)


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def _platform(principal: Principal) -> TenantContext:
    if (
        principal.tenant_context is None
        or principal.tenant_context.organisation_id is not None
        or not principal.tenant_context.platform_scope
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="platform authority required"
        )
    return principal.tenant_context


def _permission(principal: Principal, permission: str) -> TenantContext:
    context = _platform(principal)
    try:
        AuthorizationService.require_permission(context, permission)
    except AuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return context


def _error(session: Session, exc: Exception) -> HTTPException:
    session.rollback()
    if isinstance(exc, AuthorizationError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, StorageIntegrityError):
        record(Signal.STORAGE_INTEGRITY, Failure.INTEGRITY_MISMATCH)
    if isinstance(exc, (StorageIntegrityError, SourceConflict)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, IntegrityError):
        return HTTPException(status_code=409, detail="SOURCE_STATE_CONFLICT")
    if isinstance(exc, (StorageError, SQLAlchemyError)):
        return HTTPException(status_code=503, detail="SOURCE_STORAGE_UNAVAILABLE")
    return HTTPException(status_code=400, detail=str(exc))


def _durable_registry(settings: Settings) -> KnowledgeRegistryService:
    if settings.object_storage_backend != "s3":
        raise StorageError("private S3 source storage required")
    storage = build_object_storage(settings)
    if not isinstance(storage, S3CompatibleObjectStorage):
        raise StorageError("private S3 source storage required")
    return KnowledgeRegistryService(storage=storage)


def _visible_version(session: Session, version_id: uuid.UUID) -> KnowledgeDocumentVersion:
    version = session.get(KnowledgeDocumentVersion, version_id)
    if version is None or version.source.scope != KnowledgeScope.PLATFORM_SHARED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="version not found")
    return version


def _job_summary(job: KnowledgeIngestionJob) -> dict[str, object]:
    return {
        "id": str(job.id),
        "status": job.status.value,
        "pages_detected": job.pages_detected,
        "parser_name": job.parser_name,
        "parser_version": job.parser_version,
        "error_code": job.error_code,
    }


def _rights_summary(legal: KnowledgeLegalMetadata | None) -> dict[str, object]:
    return {
        "legal_review_status": legal.legal_review_status.value if legal else "NOT_REVIEWED",
        "decision_checksum": legal.decision_checksum if legal else None,
        "rights_holder": legal.rights_holder if legal else None,
        "licence_type": legal.licence_type if legal else None,
        "retention_policy": legal.retention_policy if legal else None,
        "usage_rights": legal.usage_rights if legal else None,
        "ingestion_allowed": legal.ingestion_allowed is True if legal else False,
        "approved_sha256": legal.approved_sha256 if legal else None,
        "derivative_use_allowed": legal.derivative_use_allowed is True if legal else False,
        "redistribution_allowed": legal.redistribution_allowed is True if legal else False,
        "ai_processing_allowed": legal.ai_processing_allowed is True if legal else False,
        "legal_reviewed_at": legal.legal_reviewed_at if legal else None,
        "legal_reviewed_by_user_id": str(legal.legal_reviewed_by_user_id)
        if legal and legal.legal_reviewed_by_user_id
        else None,
    }


@router.get("/authorities")
def list_authorities(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, object]]:
    _permission(principal, "standards.authority.read")
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
    except (ValueError, AuthorizationError, SQLAlchemyError) as exc:
        raise _error(session, exc) from exc
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
    except (ValueError, AuthorizationError, SQLAlchemyError) as exc:
        raise _error(session, exc) from exc
    return {"id": str(source.id), "source_code": source.source_code, "title": source.title}


@router.get("/sources")
def list_sources(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, object]]:
    _permission(principal, "standards.source.read")
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
            .where(KnowledgeSource.scope == KnowledgeScope.PLATFORM_SHARED)
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
    _permission(principal, "standards.authority.read")
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
    _permission(principal, "standards.source.read")
    source = session.get(KnowledgeSource, source_id)
    if source is None or source.scope != KnowledgeScope.PLATFORM_SHARED:
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
        "authority_id": str(source.authority_id),
        "publisher": source.authority.name,
        "canonical_url": source.canonical_url,
    }


@router.get("/sources/{source_id}/versions")
def list_source_versions(
    source_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, object]]:
    _permission(principal, "standards.source.read")
    source = session.get(KnowledgeSource, source_id)
    if source is None or source.scope != KnowledgeScope.PLATFORM_SHARED:
        raise HTTPException(status_code=404, detail="source not found")
    return [
        {
            "id": str(version.id),
            "version_label": version.version_label,
            "status": version.status.value,
            "content_mode": version.content_mode,
            "metadata_checksum": version.metadata_checksum,
            "sha256": version.sha256,
            "publication_date": version.publication_date,
            "retrieved_at": version.retrieved_at,
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
    _permission(principal, "standards.document.read")
    version = _visible_version(session, version_id)
    job = session.scalar(
        select(KnowledgeIngestionJob)
        .where(KnowledgeIngestionJob.document_version_id == version.id)
        .order_by(KnowledgeIngestionJob.created_at.desc(), KnowledgeIngestionJob.id.desc())
        .limit(1)
    )
    return {
        "id": str(version.id),
        "source_id": str(version.source_id),
        "publisher": version.source.authority.name,
        "version_label": version.version_label,
        "status": version.status.value,
        "content_mode": version.content_mode,
        "metadata_checksum": version.metadata_checksum,
        "sha256": version.sha256,
        "file_size": version.file_size,
        "mime_type": version.mime_type,
        "publication_date": version.publication_date,
        "retrieved_at": version.retrieved_at,
        "effective_from": version.effective_from,
        "effective_until": version.effective_until,
        "source_url": version.source_url,
        "latest_ingestion": _job_summary(job) if job else None,
        **_rights_summary(version.legal_metadata),
    }


@router.get("/versions/{version_id}/pages")
def list_version_pages(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, object]]:
    _permission(principal, "standards.document.read")
    _visible_version(session, version_id)
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
    _permission(principal, "standards.document.read")
    version = _visible_version(session, version_id)
    source = session.get(KnowledgeSource, version.source_id)
    authority = session.get(KnowledgeAuthority, source.authority_id) if source else None
    return {
        "authority": authority.code if authority else None,
        "source_code": source.source_code if source else None,
        "document_version_id": str(version.id),
        "version_label": version.version_label,
        "sha256": version.sha256,
        "metadata_checksum": version.metadata_checksum,
        "content_mode": version.content_mode,
        "publication_date": version.publication_date,
        "retrieved_at": version.retrieved_at,
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
    except (ValueError, AuthorizationError, SQLAlchemyError) as exc:
        raise _error(session, exc) from exc
    return {
        "id": str(version.id),
        "version_label": version.version_label,
        "status": version.status.value,
        "content_mode": version.content_mode,
        "metadata_checksum": version.metadata_checksum,
    }


@router.post("/versions/{version_id}/rights-review")
def review_rights(
    version_id: uuid.UUID,
    payload: RightsReviewRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _permission(principal, "standards.document.review")
    try:
        legal = _registry.review_rights(
            session, context, version_id=version_id, **payload.model_dump()
        )
    except (ValueError, AuthorizationError, SQLAlchemyError) as exc:
        raise _error(session, exc) from exc
    version = _visible_version(session, version_id)
    return {"id": str(version.id), "status": version.status.value, **_rights_summary(legal)}


@router.post("/versions/{version_id}/file")
def register_file(
    version_id: uuid.UUID,
    payload: FileRegisterRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    context = _permission(principal, "standards.document.manage")
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
        version = _durable_registry(settings).register_file(
            session,
            context,
            version_id=version_id,
            filename=payload.filename,
            content=content,
            declared_mime_type=payload.declared_mime_type,
        )
    except (ValueError, AuthorizationError, SQLAlchemyError) as exc:
        raise _error(session, exc) from exc
    return {
        "id": str(version.id),
        "sha256": version.sha256,
        "mime_type": version.mime_type,
        "file_size": version.file_size,
    }


@router.post("/versions/{version_id}/ingest")
def ingest_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    context = _permission(principal, "standards.document.manage")
    try:
        job = _durable_registry(settings).ingest(session, context, version_id=version_id)
    except (ValueError, AuthorizationError, SQLAlchemyError) as exc:
        raise _error(session, exc) from exc
    return _job_summary(job)


@router.get("/versions/{version_id}/integrity")
def verify_file(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    context = _permission(principal, "standards.document.read")
    try:
        version = _durable_registry(settings).verify_file(session, context, version_id=version_id)
    except (ValueError, AuthorizationError, SQLAlchemyError) as exc:
        raise _error(session, exc) from exc
    return {
        "id": str(version.id),
        "sha256": version.sha256,
        "file_size": version.file_size,
        "integrity": "VERIFIED",
        "storage_backend": "S3_PRIVATE",
    }


@router.post("/versions/{version_id}/approve")
def approve_version(
    version_id: uuid.UUID,
    payload: ApproveRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    context = _permission(principal, "standards.document.review")
    try:
        version = _durable_registry(settings).approve(
            session,
            context,
            version_id=version_id,
            expected_sha256=payload.expected_sha256,
        )
    except (ValueError, AuthorizationError, SQLAlchemyError) as exc:
        raise _error(session, exc) from exc
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
    except (ValueError, AuthorizationError, SQLAlchemyError) as exc:
        raise _error(session, exc) from exc
    return {"id": str(version.id), "status": version.status.value}
