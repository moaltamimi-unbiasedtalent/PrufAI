"""Scope-bound operation receipts shared by feedback commands."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationError, TenantContext
from app.evaluation import service as evaluation
from app.evaluation.contracts import Contract
from app.models.feedback import FeedbackOperation
from app.writing_assessment.service import canonical_checksum

utcnow = evaluation.utcnow


class Pin(Contract):
    id: uuid.UUID
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class FeedbackConflict(ValueError):
    pass


def require_platform(session: Session, context: TenantContext, *permissions: str) -> TenantContext:
    return evaluation.require(session, context, *permissions)


def _checksum(row: FeedbackOperation) -> str:
    return canonical_checksum(
        {
            "id": str(row.id),
            "organisation_id": str(row.organisation_id) if row.organisation_id else None,
            "actor_id": str(row.actor_id),
            "entity_kind": row.entity_kind,
            "entity_id": str(row.entity_id),
            "revision": row.revision,
            "action": row.action,
            "request_checksum": row.request_checksum,
            "previous_checksum": row.previous_checksum,
            "state": row.state,
        }
    )


def verify_receipt(row: FeedbackOperation) -> FeedbackOperation:
    if row.checksum != _checksum(row):
        raise FeedbackConflict("FEEDBACK_RECEIPT_CHECKSUM_MISMATCH")
    return row


def operation(
    session: Session,
    context: TenantContext,
    operation_id: uuid.UUID,
    action: str,
    request: dict[str, Any],
) -> FeedbackOperation | None:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": operation_id.int % (2**63 - 1)},
        )
    row = session.get(FeedbackOperation, operation_id)
    if row is None:
        return None
    if row.actor_id != context.user_id or row.organisation_id != context.organisation_id:
        raise AuthorizationError("FEEDBACK_OPERATION_FORBIDDEN")
    if row.action != action or row.request_checksum != canonical_checksum(request):
        raise FeedbackConflict("FEEDBACK_OPERATION_CONFLICT")
    return verify_receipt(row)


def latest(session: Session, entity_kind: str, entity_id: uuid.UUID) -> FeedbackOperation:
    row = session.scalar(
        select(FeedbackOperation)
        .where(
            FeedbackOperation.entity_kind == entity_kind, FeedbackOperation.entity_id == entity_id
        )
        .order_by(FeedbackOperation.revision.desc())
        .limit(1)
    )
    if row is None:
        raise FeedbackConflict("FEEDBACK_STATE_UNAVAILABLE")
    return verify_receipt(row)


def record(
    session: Session,
    context: TenantContext,
    operation_id: uuid.UUID,
    action: str,
    request: dict[str, Any],
    *,
    entity_kind: str,
    entity_id: uuid.UUID,
    state: dict[str, Any],
    previous: FeedbackOperation | None = None,
    audit_action: str | None = None,
) -> FeedbackOperation:
    if previous is not None:
        if (
            previous.entity_id != entity_id
            or previous.entity_kind != entity_kind
            or previous.organisation_id != context.organisation_id
        ):
            raise FeedbackConflict("FEEDBACK_PREDECESSOR_MISMATCH")
        verify_receipt(previous)
    row = FeedbackOperation(
        id=operation_id,
        organisation_id=context.organisation_id,
        actor_id=context.user_id,
        entity_kind=entity_kind,
        entity_id=entity_id,
        revision=previous.revision + 1 if previous else 1,
        action=action,
        request_checksum=canonical_checksum(request),
        previous_checksum=previous.checksum if previous else None,
        state=state,
        created_at=utcnow(),
    )
    row.checksum = _checksum(row)
    session.add(row)
    session.flush()
    AuditService.record(
        session,
        context,
        event_type=f"feedback.{audit_action or action}",
        entity_type=entity_kind.lower(),
        organisation_id=context.organisation_id,
        entity_id=str(entity_id),
        entity_version=str(row.revision),
        request_id=str(operation_id),
        previous_state_hash=row.previous_checksum,
        new_state_hash=row.checksum,
        metadata={"status": state.get("status"), "production_activation": False},
        require_audit_permission=False,
    )
    return row
