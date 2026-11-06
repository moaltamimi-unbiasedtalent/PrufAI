"""Tenant-authorized Control Centre workflow for the Berlin editorial set."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, build_tenant_context
from app.db import get_db_session
from app.lid_editorial import collections, views
from app.lid_editorial.access import editorial_context, load_collection
from app.lid_editorial.publication import decide_pack
from app.lid_editorial.reviews import current_item_evidence
from app.models import QuestionBankItemVersion
from app.models.lid_editorial import LidEditorialCollection, LidEditorialItemVersion
from app.writing_assessment.service import canonical_checksum

router = APIRouter(prefix="/api/v1/platform/standards/lid/editorial", tags=["lid-editorial"])
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CreateCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organisation_id: uuid.UUID
    blueprint_version_id: uuid.UUID
    expected_blueprint_checksum: Digest
    operation_id: uuid.UUID


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_collection_checksum: Digest
    bundesland: str = Field(min_length=5, max_length=5)
    reviewed_only: bool = False


class PackDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_collection_checksum: Digest
    operation_id: uuid.UUID


class SuccessorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_checksum: Digest
    operation_id: uuid.UUID
    prompt: str = Field(min_length=10, max_length=700)
    options: list[Annotated[str, Field(min_length=1, max_length=250)]] = Field(
        min_length=4, max_length=4
    )
    correct_index: int = Field(strict=True, ge=0, le=3)
    rationale: str = Field(min_length=10, max_length=1500)
    replacement_source: collections.SourceReplacement | None = None


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, (AuthorizationError, PermissionError)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, IntegrityError):
        return HTTPException(status_code=409, detail="LID_EDITORIAL_PERSISTENCE_CONFLICT")
    reason = str(exc)
    return HTTPException(status_code=404 if reason.endswith("NOT_FOUND") else 409, detail=reason)


@router.get("/collections")
def list_collections(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    try:
        context = editorial_context(session, principal, organisation_id, "question_bank.read")
        query = (
            select(LidEditorialCollection)
            .where(LidEditorialCollection.organisation_id == organisation_id)
            .order_by(LidEditorialCollection.created_at.desc())
            .limit(50)
        )
        return [
            views.collection_detail(session, row, context, include_items=False)
            for row in session.scalars(query)
        ]
    except (AuthorizationError, ValueError) as exc:
        raise _error(exc) from exc


@router.post("/collections", status_code=201)
def create_collection(
    payload: CreateCollection,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        context = editorial_context(
            session, principal, payload.organisation_id, "question_bank.manage"
        )
        platform = build_tenant_context(session, principal.user.id)
        row = collections.create_collection(
            session,
            context,
            platform,
            blueprint_version_id=payload.blueprint_version_id,
            expected_blueprint_checksum=payload.expected_blueprint_checksum,
            operation_id=payload.operation_id,
        )
        result = views.collection_detail(session, row, context)
        session.commit()
        return result
    except (AuthorizationError, ValueError, IntegrityError) as exc:
        session.rollback()
        raise _error(exc) from exc


@router.get("/collections/{collection_id}")
def collection_detail(
    collection_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        row, context = load_collection(session, principal, collection_id, "question_bank.read")
        return views.collection_detail(session, row, context)
    except (AuthorizationError, ValueError) as exc:
        raise _error(exc) from exc


@router.get("/collections/{collection_id}/items/{item_version_id}")
def item_detail(
    collection_id: uuid.UUID,
    item_version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        row, context = load_collection(session, principal, collection_id, "question_bank.read")
        if not any(
            AuthorizationService.has_permission(context, permission, row.organisation_id)
            for permission in ("question_bank.manage", "question_bank.review")
        ):
            raise AuthorizationError("LID_EDITORIAL_ITEM_REVIEW_PERMISSION_REQUIRED")
        extension = session.get(LidEditorialItemVersion, item_version_id)
        version = session.get(QuestionBankItemVersion, item_version_id)
        if extension is None or version is None:
            raise ValueError("LID_EDITORIAL_ITEM_NOT_FOUND")
        return views.item_detail(session, row, extension, version, context)
    except (AuthorizationError, ValueError) as exc:
        raise _error(exc) from exc


@router.post("/collections/{collection_id}/items/{item_version_id}/successor", status_code=201)
def item_successor(
    collection_id: uuid.UUID,
    item_version_id: uuid.UUID,
    payload: SuccessorRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        row, context = load_collection(session, principal, collection_id, "question_bank.manage")
        extension = collections.successor(
            session,
            context,
            row,
            item_version_id,
            replacement_source=payload.replacement_source,
            **payload.model_dump(exclude={"replacement_source"}),
        )
        version = session.get(QuestionBankItemVersion, extension.item_version_id)
        if version is None:
            raise ValueError("LID_EDITORIAL_ITEM_NOT_FOUND")
        result = views.item_detail(session, row, extension, version, context)
        session.commit()
        return result
    except (AuthorizationError, ValueError, IntegrityError) as exc:
        session.rollback()
        raise _error(exc) from exc


@router.post("/collections/{collection_id}/preview")
def preview(
    collection_id: uuid.UUID,
    payload: PreviewRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        row, context = load_collection(session, principal, collection_id, "question_bank.review")
        checksum, items = collections.preview_order(
            session, row, payload.bundesland, payload.expected_collection_checksum
        )
        for _, version in items:
            evidence = current_item_evidence(
                session, version, mode=collections.MODE, lock_for_review=False
            )
            if not evidence["preview_eligible"] or (
                payload.reviewed_only
                and not (evidence["test_approved"] or evidence["genuine_approved"])
            ):
                raise ValueError("PRACTICE_FORM_NOT_READY")
        manifest = {
            "mode": collections.MODE,
            "reviewed_only": payload.reviewed_only,
            "collection_checksum": checksum,
            "bundesland": payload.bundesland,
            "duration_seconds": 3600,
            "blueprint_version_id": str(row.blueprint_version_id),
            "pack_version_id": str(row.pack_version_id),
            "items": [{"id": str(item.id), "checksum": pin.checksum} for pin, item in items],
        }
        digest = canonical_checksum(manifest)
        result = {
            **manifest,
            "checksum": digest,
            "items": [
                {
                    **views.item_detail(session, row, pin, version, context, include_history=False),
                    "slot": index,
                }
                for index, (pin, version) in enumerate(items, start=1)
            ],
            "official_certification": False,
            "publication_status": "EDITORIAL_PREVIEW_ONLY",
        }
        collections._audit(
            session,
            context,
            "previewed",
            row,
            {
                "preview_checksum": digest,
                "collection_checksum": checksum,
                "item_count": 33,
            },
        )
        session.commit()
        return result
    except (AuthorizationError, PermissionError, ValueError) as exc:
        session.rollback()
        raise _error(exc) from exc


def _decide(
    session: Session,
    principal: Principal,
    collection_id: uuid.UUID,
    payload: PackDecisionRequest,
    action: str,
) -> dict[str, Any]:
    permission = "standard_pack.publish" if action == "PUBLISHED" else "standard_pack.review"
    try:
        row, context = load_collection(session, principal, collection_id, permission)
    except (AuthorizationError, ValueError) as exc:
        raise _error(exc) from exc
    try:
        decision = decide_pack(session, context, row, action=action, **payload.model_dump())
        result = {
            **views.collection_detail(session, row, context),
            "decision_id": str(decision.id),
            "decision_checksum": decision.checksum,
        }
        session.commit()
        return result
    except (AuthorizationError, ValueError, IntegrityError) as exc:
        session.rollback()
        if isinstance(exc, ValueError):
            collections._audit(
                session,
                context,
                "publication_denied",
                row,
                {
                    "failure_code": "LID_EDITORIAL_PUBLICATION_GATE_BLOCKED",
                    "operation_id": str(payload.operation_id),
                    "decision_category": action,
                },
            )
            session.commit()
        raise _error(exc) from exc


@router.post("/collections/{collection_id}/approve")
def approve(
    collection_id: uuid.UUID,
    payload: PackDecisionRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _decide(session, principal, collection_id, payload, "APPROVED")


@router.post("/collections/{collection_id}/publish")
def publish(
    collection_id: uuid.UUID,
    payload: PackDecisionRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _decide(session, principal, collection_id, payload, "PUBLISHED")
