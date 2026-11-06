"""Authenticated exact item review submissions and decisions."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError
from app.db import get_db_session
from app.lid_editorial import reviews
from app.lid_editorial.access import load_collection
from app.lid_editorial.review_contracts import (
    REVIEW_PERMISSIONS,
    SUBMIT_PERMISSION,
    ReviewDecision,
    ReviewSubmission,
)
from app.models.lid_editorial_review import LidEditorialReviewReceipt

router = APIRouter(
    prefix="/api/v1/platform/standards/lid/editorial/collections",
    tags=["lid-editorial"],
)


def _write(session: Session, callback: Callable[[], LidEditorialReviewReceipt]) -> dict[str, Any]:
    try:
        response = reviews.receipt_summary(callback())
        session.commit()
        return response
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except reviews.EditorialConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="LID_EDITORIAL_REVIEW_CONFLICT") from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(
            status_code=404 if str(exc).endswith("_NOT_FOUND") else 422, detail=str(exc)
        ) from exc


@router.post("/{collection_id}/items/{version_id}/review/submit")
def submit(
    collection_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: ReviewSubmission,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    def operation() -> LidEditorialReviewReceipt:
        collection, context = load_collection(session, principal, collection_id, SUBMIT_PERMISSION)
        return reviews.submit_review(session, collection, context, version_id, payload)

    return _write(session, operation)


@router.post("/{collection_id}/items/{version_id}/review/decision")
def decide(
    collection_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: ReviewDecision,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    def operation() -> LidEditorialReviewReceipt:
        collection, context = load_collection(
            session, principal, collection_id, REVIEW_PERMISSIONS[payload.stage]
        )
        return reviews.decide_review(session, collection, context, version_id, payload)

    return _write(session, operation)
