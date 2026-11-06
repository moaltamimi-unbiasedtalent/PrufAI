"""Platform preview endpoints and a separate approved-only institution projection."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, TenantContext
from app.authorization.membership import require_active_tenant_permission
from app.db import get_db_session
from app.dtz_policy import DTZ_EXAM_FAMILY, DTZ_PACK_CODE
from app.models import StandardPack, StandardPackVersion
from app.models.enums import StandardPackStatus
from app.models.standard_pack import DtzBlueprintProvenance
from app.standard_packs.dtz_governance import DtzPreviewRequest, evaluate_dtz_preview
from app.standard_packs.dtz_service import (
    create_dtz_blueprint,
    dtz_detail,
    dtz_draft_integrity,
    dtz_summary,
    dtz_version,
)
from app.standard_packs.dtz_sources import DTZ_ENTRIES, dtz_sources, register_dtz_source

router = APIRouter(prefix="/api/v1/platform/standards/dtz", tags=["standards-dtz"])
institution_router = APIRouter(prefix="/api/v1/organisations", tags=["institution-standards"])


class DtzSourcePin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: uuid.UUID
    metadata_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class DtzCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: dict[str, DtzSourcePin] = Field(
        min_length=len(DTZ_ENTRIES), max_length=len(DTZ_ENTRIES)
    )


def _platform(principal: Principal, *permissions: str) -> TenantContext:
    context = principal.tenant_context
    if (
        context is None
        or context.organisation_id is not None
        or not context.platform_scope
        or not all(permission in context.platform_permissions for permission in permissions)
    ):
        raise HTTPException(
            status_code=403, detail="explicit platform standards authority required"
        )
    return context


def _version(session: Session, version_id: uuid.UUID) -> StandardPackVersion:
    item = dtz_version(session, version_id)
    if item is None:
        raise HTTPException(status_code=404, detail="DTZ draft not found")
    return item


@router.get("/sources")
def list_dtz_sources(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db_session)
) -> list[dict[str, Any]]:
    _platform(principal, "standards.source.read", "standards.document.read")
    return dtz_sources(session)


@router.post("/sources/{key}/register")
def register_source(
    key: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _platform(
        principal,
        "standards.authority.manage",
        "standards.source.manage",
        "standards.document.manage",
        "standards.document.read",
    )
    if key not in DTZ_ENTRIES:
        raise HTTPException(status_code=404, detail="DTZ source not found")
    try:
        row = register_dtz_source(session, context, key)
        projected = next(item for item in dtz_sources(session) if item["key"] == key)
        if not projected["registered"] or projected["registered"]["id"] != str(row.id):
            raise ValueError("DTZ_SOURCE_IDENTITY_CONFLICT")
        session.commit()
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(
            status_code=403, detail="platform standards permission required"
        ) from exc
    except (ValueError, IntegrityError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="DTZ_SOURCE_IDENTITY_CONFLICT") from exc
    return projected


@router.get("/blueprints")
def list_dtz_blueprints(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db_session)
) -> list[dict[str, Any]]:
    _platform(principal, "standard_pack.read", "standards.document.read")
    query = (
        select(StandardPackVersion)
        .join(DtzBlueprintProvenance)
        .order_by(DtzBlueprintProvenance.recorded_at.desc(), StandardPackVersion.id)
        .limit(50)
    )
    return [dtz_summary(row) for row in session.scalars(query)]


@router.post("/blueprints", status_code=201)
def create_blueprint(
    payload: DtzCreateRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _platform(
        principal,
        "standard_pack.manage",
        "standard_pack.read",
        "standards.document.read",
        "rules.manage",
    )
    try:
        pins = {
            key: (value.version_id, value.metadata_checksum)
            for key, value in payload.sources.items()
        }
        version = create_dtz_blueprint(session, context, pins)
        result = dtz_detail(session, version)
        session.commit()
        return result
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="DTZ_DRAFT_EXACT_REPLAY_CONFLICT") from exc


@router.get("/blueprints/{version_id}")
def get_blueprint(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    _platform(principal, "standard_pack.read", "standards.document.read")
    return dtz_detail(session, _version(session, version_id))


@router.post("/blueprints/{version_id}/preview")
def preview_blueprint(
    version_id: uuid.UUID,
    payload: DtzPreviewRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _platform(
        principal, "standard_pack.read", "standards.document.read", "rules.evaluate"
    )
    version = _version(session, version_id)
    reason = (
        "DTZ_PACK_CHECKSUM_MISMATCH"
        if payload.expected_pack_checksum != version.configuration_checksum
        else dtz_draft_integrity(session, version)
    )
    provenance = version.dtz_provenance
    if reason or provenance is None:
        result: dict[str, Any] = {
            "outcome": "UNDETERMINED",
            "reason": reason or "DTZ_DRAFT_PROVENANCE_INVALID",
            "execution_class": "DRAFT_PREVIEW",
            "rule": None,
        }
    else:
        result = evaluate_dtz_preview(session, version, provenance.manifest["artifacts"], payload)
    result["official_result_created"] = False
    AuditService.record_security(
        session,
        event_type="standards.dtz_draft.previewed",
        entity_type="standard_pack",
        entity_id=str(version.id),
        actor_id=context.user_id,
        metadata={
            "pack_checksum": version.configuration_checksum,
            "outcome": result["outcome"],
            "reason": result["reason"],
        },
    )
    session.commit()
    return result


@institution_router.get("/{organisation_id}/standards/dtz")
def institution_dtz_metadata(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = principal.tenant_context
    if context is None:
        raise HTTPException(status_code=403, detail="active institution authority required")
    try:
        require_active_tenant_permission(session, context, "exam.read", organisation_id)
    except AuthorizationError as exc:
        raise HTTPException(
            status_code=403, detail="active institution authority required"
        ) from exc
    # S2 contributes no eligible version. Private draft and provenance tables
    # are not selected or joined by this institution projection.
    query = (
        select(StandardPackVersion)
        .join(StandardPack)
        .where(
            StandardPack.code == DTZ_PACK_CODE,
            StandardPack.status.in_([StandardPackStatus.PUBLISHED, StandardPackStatus.ACTIVE]),
            StandardPackVersion.status.in_(
                [StandardPackStatus.PUBLISHED, StandardPackStatus.ACTIVE]
            ),
        )
        .order_by(StandardPackVersion.version_label)
        .limit(50)
    )
    return {
        "exam_family": DTZ_EXAM_FAMILY,
        "published_versions": [
            {
                "version_label": item.version_label,
                "supported_levels": item.supported_levels,
                "status": item.status.value,
            }
            for item in session.scalars(query)
        ],
    }
