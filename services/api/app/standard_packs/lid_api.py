"""Platform-only LiD metadata, draft review and numerical previews."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError
from app.db import get_db_session
from app.models import StandardPackVersion
from app.models.standard_pack import LidBlueprintProvenance
from app.standard_packs.dtz_api import DtzSourcePin, _platform
from app.standard_packs.lid_service import (
    create_lid_blueprint,
    evaluate_lid_blueprint,
    lid_detail,
    lid_summary,
    lid_version,
)
from app.standard_packs.lid_sources import LID_ENTRIES, lid_sources, register_lid_source

router = APIRouter(prefix="/api/v1/platform/standards/lid", tags=["standards-lid"])


class LidDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: dict[str, DtzSourcePin] = Field(
        min_length=len(LID_ENTRIES), max_length=len(LID_ENTRIES)
    )


class LidPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_pack_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: date
    raw_correct: int = Field(strict=True, ge=0, le=33)


def _version(session: Session, version_id: uuid.UUID) -> StandardPackVersion:
    version = lid_version(session, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="LiD draft not found")
    return version


@router.get("/sources")
def list_sources(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db_session)
) -> list[dict[str, Any]]:
    _platform(principal, "standards.source.read", "standards.document.read")
    return lid_sources(session)


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
    if key not in LID_ENTRIES:
        raise HTTPException(status_code=404, detail="LiD source not found")
    try:
        document = register_lid_source(session, context, key)
        result = next(item for item in lid_sources(session) if item["key"] == key)
        if not result["registered"] or result["registered"]["id"] != str(document.id):
            raise ValueError("LID_SOURCE_IDENTITY_CONFLICT")
        session.commit()
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(
            status_code=403, detail="platform standards permission required"
        ) from exc
    except (ValueError, IntegrityError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="LID_SOURCE_IDENTITY_CONFLICT") from exc
    return result


@router.get("/blueprints")
def list_blueprints(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_db_session)
) -> list[dict[str, Any]]:
    _platform(principal, "standard_pack.read", "standards.document.read")
    query = (
        select(StandardPackVersion)
        .join(LidBlueprintProvenance)
        .order_by(LidBlueprintProvenance.recorded_at.desc(), StandardPackVersion.id)
        .limit(50)
    )
    return [lid_summary(version) for version in session.scalars(query)]


@router.post("/blueprints", status_code=201)
def create_blueprint(
    payload: LidDraftRequest,
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
            key: (pin.version_id, pin.metadata_checksum) for key, pin in payload.sources.items()
        }
        version = create_lid_blueprint(session, context, pins)
        result = lid_detail(session, version)
        session.commit()
        return result
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="LID_DRAFT_EXACT_REPLAY_CONFLICT") from exc


@router.get("/blueprints/{version_id}")
def get_blueprint(
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    _platform(principal, "standard_pack.read", "standards.document.read")
    return lid_detail(session, _version(session, version_id))


@router.post("/blueprints/{version_id}/preview")
def preview_blueprint(
    version_id: uuid.UUID,
    payload: LidPreviewRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _platform(
        principal, "standard_pack.read", "standards.document.read", "rules.evaluate"
    )
    version = _version(session, version_id)
    result = evaluate_lid_blueprint(
        session,
        version,
        raw_correct=payload.raw_correct,
        answered_count=33,
        as_of=datetime.combine(payload.as_of, time.min, ZoneInfo("Europe/Berlin")),
        expected_pack_checksum=payload.expected_pack_checksum,
    )
    AuditService.record_security(
        session,
        event_type="standards.lid_draft.previewed",
        entity_type="standard_pack",
        entity_id=str(version.id),
        actor_id=context.user_id,
        metadata={
            "pack_checksum": version.configuration_checksum,
            "reason": result.get("reason"),
            "official_outcome": result["official_outcome"],
        },
    )
    session.commit()
    return result
