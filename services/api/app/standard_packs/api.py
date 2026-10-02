from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService
from app.db import get_db_session
from app.models import (
    StandardPack,
    StandardPackModule,
    StandardPackSection,
    StandardPackTaskSpecification,
    StandardPackVersion,
)
from app.models.enums import (
    AssessmentFamily,
    PackMediaRequirement,
    PackNavigationMode,
    PackResponseMode,
    StandardPackBindingType,
    StandardPackStatus,
)
from app.standard_packs.service import StandardPackService

router = APIRouter(prefix="/api/v1/platform", tags=["standard-packs"])


class PackCreateRequest(BaseModel):
    code: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=1, max_length=240)
    assessment_family: AssessmentFamily
    authority_id: Optional[uuid.UUID] = None
    exam_family: Optional[str] = Field(default=None, max_length=120)
    language: Optional[str] = Field(default=None, max_length=20)
    description: Optional[str] = None


class VersionCreateRequest(BaseModel):
    version_label: str = Field(min_length=1, max_length=120)
    effective_from: Optional[datetime] = None
    effective_until: Optional[datetime] = None
    supersedes_version_id: Optional[uuid.UUID] = None
    supported_levels: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class BindingRequest(BaseModel):
    binding_type: StandardPackBindingType
    knowledge_source_id: Optional[uuid.UUID] = None
    document_version_id: Optional[uuid.UUID] = None
    purpose: Optional[str] = None
    required: bool = True
    notes: Optional[str] = None


class ModuleRequest(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=240)
    skill: Optional[str] = None
    sequence: int = Field(ge=0)
    required: bool = True
    time_limit_seconds: Optional[int] = Field(default=None, ge=0)
    preparation_time_seconds: Optional[int] = Field(default=None, ge=0)
    navigation_mode: PackNavigationMode = PackNavigationMode.LINEAR


class SectionRequest(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=240)
    sequence: int = Field(ge=0)
    instructions_ref: Optional[str] = None
    time_limit_seconds: Optional[int] = Field(default=None, ge=0)
    navigation_mode: PackNavigationMode = PackNavigationMode.LINEAR


class TaskRequest(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=240)
    sequence: int = Field(ge=0)
    task_type: str = Field(min_length=1, max_length=60)
    min_items: int = Field(default=1, ge=0)
    max_items: int = Field(default=1, ge=0)
    required_items: Optional[int] = Field(default=None, ge=0)
    points_per_item: Optional[int] = Field(default=None, ge=0)
    response_mode: PackResponseMode
    media_requirement: PackMediaRequirement = PackMediaRequirement.NONE
    generation_allowed: bool = False
    selection_allowed: bool = False
    rubric_required: bool = False
    rubric_reference: Optional[str] = None
    metadata: dict[str, object] = Field(default_factory=dict)


def _platform(principal: Principal):
    if principal.tenant_context is None or not principal.tenant_context.platform_scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="platform authority required"
        )
    return principal.tenant_context


def _require(principal: Principal, permission: str):
    context = _platform(principal)
    try:
        AuthorizationService.require_permission(context, permission)
    except AuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return context


def _pack(item: StandardPack) -> dict[str, object]:
    return {
        "id": str(item.id),
        "code": item.code,
        "name": item.name,
        "assessment_family": item.assessment_family.value,
        "authority_id": str(item.authority_id) if item.authority_id else None,
        "exam_family": item.exam_family,
        "language": item.language,
        "status": item.status.value,
    }


def _version(item: StandardPackVersion) -> dict[str, object]:
    return {
        "id": str(item.id),
        "standard_pack_id": str(item.standard_pack_id),
        "version_label": item.version_label,
        "status": item.status.value,
        "effective_from": item.effective_from,
        "effective_until": item.effective_until,
        "checksum": item.configuration_checksum,
        "supported_levels": item.supported_levels,
        "allowed_classifications": item.allowed_classifications,
    }


@router.get("/standard-packs")
def list_packs(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    status_filter: Optional[StandardPackStatus] = Query(default=None, alias="status"),
    assessment_family: Optional[AssessmentFamily] = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, object]]:
    _require(principal, "standard_pack.read")
    query = select(StandardPack).order_by(StandardPack.code).offset(offset).limit(limit)
    if status_filter is not None:
        query = query.where(StandardPack.status == status_filter)
    if assessment_family is not None:
        query = query.where(StandardPack.assessment_family == assessment_family)
    return [_pack(item) for item in session.scalars(query)]


@router.post("/standard-packs", status_code=status.HTTP_201_CREATED)
def create_pack(
    payload: PackCreateRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _require(principal, "standard_pack.manage")
    try:
        item = StandardPackService.create_pack(session, context=context, **payload.model_dump())
        session.commit()
    except (ValueError, AuthorizationError) as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _pack(item)


@router.get("/standard-packs/{pack_id}")
def get_pack(
    pack_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require(principal, "standard_pack.read")
    item = session.get(StandardPack, pack_id)
    if item is None:
        raise HTTPException(status_code=404, detail="standard pack not found")
    return _pack(item)


@router.post("/standard-packs/{pack_id}/versions", status_code=status.HTTP_201_CREATED)
def create_version(
    pack_id: uuid.UUID,
    payload: VersionCreateRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _require(principal, "standard_pack.manage")
    pack = session.get(StandardPack, pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="standard pack not found")
    try:
        item = StandardPackService.create_version(
            session,
            pack,
            created_by_user_id=principal.user.id,
            context=context,
            **payload.model_dump(),
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _version(item)


@router.get("/standard-packs/{pack_id}/versions")
def list_versions(
    pack_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict[str, object]]:
    _require(principal, "standard_pack.read")
    return [
        _version(item)
        for item in session.scalars(
            select(StandardPackVersion)
            .where(StandardPackVersion.standard_pack_id == pack_id)
            .order_by(StandardPackVersion.version_label)
            .offset(offset)
            .limit(limit)
        )
    ]


@router.get("/standard-pack-versions/{version_id}")
def get_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require(principal, "standard_pack.read")
    item = session.get(StandardPackVersion, version_id)
    if item is None:
        raise HTTPException(status_code=404, detail="standard pack version not found")
    return _version(item)


@router.post(
    "/standard-pack-versions/{version_id}/source-bindings", status_code=status.HTTP_201_CREATED
)
def bind_source(
    version_id: uuid.UUID,
    payload: BindingRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _require(principal, "standard_pack.manage")
    version = session.get(StandardPackVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="standard pack version not found")
    try:
        binding = StandardPackService.bind_source(
            session, version, context=context, **payload.model_dump()
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": str(binding.id),
        "document_version_id": str(binding.document_version_id)
        if binding.document_version_id
        else None,
        "binding_type": binding.binding_type.value,
    }


@router.post("/standard-pack-versions/{version_id}/modules", status_code=status.HTTP_201_CREATED)
def add_module(
    version_id: uuid.UUID,
    payload: ModuleRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require(principal, "standard_pack.manage")
    version = session.get(StandardPackVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="standard pack version not found")
    try:
        StandardPackService.ensure_mutable(version)
        item = StandardPackModule(
            standard_pack_version_id=version.id,
            created_at=datetime.now(timezone.utc),
            **payload.model_dump(),
        )
        session.add(item)
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": str(item.id), **payload.model_dump(mode="json")}


@router.post("/standard-pack-modules/{module_id}/sections", status_code=status.HTTP_201_CREATED)
def add_section(
    module_id: uuid.UUID,
    payload: SectionRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require(principal, "standard_pack.manage")
    module = session.get(StandardPackModule, module_id)
    if module is None:
        raise HTTPException(status_code=404, detail="standard pack module not found")
    version = session.get(StandardPackVersion, module.standard_pack_version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="standard pack version not found")
    StandardPackService.ensure_mutable(version)
    item = StandardPackSection(
        module_id=module.id,
        created_at=datetime.now(timezone.utc),
        **payload.model_dump(),
    )
    session.add(item)
    session.commit()
    return {"id": str(item.id), **payload.model_dump(mode="json")}


@router.post("/standard-pack-sections/{section_id}/tasks", status_code=status.HTTP_201_CREATED)
def add_task(
    section_id: uuid.UUID,
    payload: TaskRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require(principal, "standard_pack.manage")
    section = session.get(StandardPackSection, section_id)
    if section is None:
        raise HTTPException(status_code=404, detail="standard pack section not found")
    module = session.get(StandardPackModule, section.module_id)
    version = session.get(StandardPackVersion, module.standard_pack_version_id) if module else None
    if version is None:
        raise HTTPException(status_code=404, detail="standard pack version not found")
    StandardPackService.ensure_mutable(version)
    values = payload.model_dump()
    values["metadata_json"] = values.pop("metadata")
    item = StandardPackTaskSpecification(
        section_id=section.id, created_at=datetime.now(timezone.utc), **values
    )
    session.add(item)
    session.commit()
    return {"id": str(item.id), **payload.model_dump(mode="json")}


@router.post("/standard-pack-versions/{version_id}/validate")
def validate_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _require(principal, "standard_pack.manage")
    version = session.get(StandardPackVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="standard pack version not found")
    result = StandardPackService.validate(session, version)
    StandardPackService._audit(
        session,
        context,
        "standard_pack.validation.completed",
        version,
        {"checksum": result.checksum, "status": result.status.value},
    )
    session.commit()
    return {
        "status": result.status.value,
        "checksum": result.checksum,
        "issues": [issue.__dict__ for issue in result.issues],
    }


def _transition(
    version_id: uuid.UUID, target: StandardPackStatus, principal: Principal, session: Session
) -> dict[str, object]:
    permission = (
        "standard_pack.publish"
        if target
        in {StandardPackStatus.PUBLISHED, StandardPackStatus.ACTIVE, StandardPackStatus.RETIRED}
        else "standard_pack.review"
    )
    context = _require(principal, permission)
    version = session.get(StandardPackVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="standard pack version not found")
    try:
        result = StandardPackService.transition(
            session, version, target, actor_id=principal.user.id, context=context
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "version": _version(version),
        "validation": {
            "status": result.status.value,
            "issues": [issue.__dict__ for issue in result.issues],
        },
    }


@router.post("/standard-pack-versions/{version_id}/request-review")
def request_review(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _transition(version_id, StandardPackStatus.HUMAN_REVIEW, principal, session)


@router.post("/standard-pack-versions/{version_id}/approve")
def approve(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _transition(version_id, StandardPackStatus.APPROVED, principal, session)


@router.post("/standard-pack-versions/{version_id}/publish")
def publish(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _transition(version_id, StandardPackStatus.PUBLISHED, principal, session)


@router.post("/standard-pack-versions/{version_id}/retire")
def retire(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    return _transition(version_id, StandardPackStatus.RETIRED, principal, session)


@router.get("/standard-pack-versions/{version_id}/export")
def export_version(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    context = _require(principal, "standard_pack.read")
    version = session.get(StandardPackVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="standard pack version not found")
    payload = StandardPackService.export(version)
    StandardPackService._audit(
        session, context, "standard_pack.exported", version, {"checksum": payload["pack_checksum"]}
    )
    session.commit()
    return payload


@router.get("/standard-pack-versions/{version_id}/diff/{other_id}")
def diff_versions(
    version_id: uuid.UUID,
    other_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, object]:
    _require(principal, "standard_pack.read")
    first = session.get(StandardPackVersion, version_id)
    second = session.get(StandardPackVersion, other_id)
    if first is None or second is None:
        raise HTTPException(status_code=404, detail="standard pack version not found")
    return StandardPackService.diff(first, second)
