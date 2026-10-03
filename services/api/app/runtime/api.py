from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError
from app.db import get_db_session
from app.runtime.service import CandidateRuntimeService, RuntimeConflict, RuntimeExpired

candidate_router = APIRouter(prefix="/api/v1/candidate", tags=["candidate-runtime"])
organisation_router = APIRouter(prefix="/api/v1/organisations", tags=["candidate-assignments"])


def _context(principal: Principal):
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


class AssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_user_id: uuid.UUID
    exam_version_id: uuid.UUID
    available_from: Optional[datetime] = None
    available_until: Optional[datetime] = None
    maximum_attempts: int = Field(default=1, ge=1, le=20)


class ResponseSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_operation_id: uuid.UUID
    expected_revision: int = Field(ge=0)
    payload: dict[str, Any]
    client_observed_at: Optional[datetime] = None


def _assignment_payload(assignment) -> dict[str, Any]:
    return {
        "id": str(assignment.id),
        "status": assignment.status.value,
        "available_from": assignment.available_from,
        "available_until": assignment.available_until,
        "maximum_attempts": assignment.maximum_attempts,
        "attempts": [
            {
                "id": str(attempt.id),
                "status": attempt.status.value,
                "attempt_number": attempt.attempt_number,
            }
            for attempt in assignment.attempts
        ],
    }


def _attempt_payload(attempt) -> dict[str, Any]:
    return {
        "id": str(attempt.id),
        "status": attempt.status.value,
        "attempt_number": attempt.attempt_number,
        "started_at": attempt.started_at,
        "deadline_at": attempt.deadline_at,
        "submitted_at": attempt.submitted_at,
        "expired_at": attempt.expired_at,
        "runtime_plan_checksum": attempt.runtime_plan_checksum,
    }


def _runtime_error(exc: Exception) -> HTTPException:
    if isinstance(exc, RuntimeConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, RuntimeExpired):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (PermissionError, AuthorizationError)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@organisation_router.post(
    "/{organisation_id}/exam-assignments", status_code=status.HTTP_201_CREATED
)
def create_assignment(
    organisation_id: uuid.UUID,
    payload: AssignmentCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        assignment = CandidateRuntimeService.create_assignment(
            session, context, **payload.model_dump()
        )
        session.commit()
        return _assignment_payload(assignment)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _runtime_error(exc) from exc


@organisation_router.get("/{organisation_id}/exam-assignments")
def list_organisation_assignments(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    from app.authorization import AuthorizationService

    AuthorizationService.require_permission(context, "exam_assignment.read", organisation_id)
    from sqlalchemy import select

    from app.models import CandidateExamAssignment

    rows = session.scalars(
        select(CandidateExamAssignment)
        .where(CandidateExamAssignment.organisation_id == organisation_id)
        .order_by(CandidateExamAssignment.created_at.desc())
    )
    return [_assignment_payload(row) for row in rows]


@organisation_router.post("/{organisation_id}/exam-assignments/{assignment_id}/cancel")
def cancel_assignment(
    organisation_id: uuid.UUID,
    assignment_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        assignment = CandidateRuntimeService.cancel_assignment(session, context, assignment_id)
        session.commit()
        return _assignment_payload(assignment)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _runtime_error(exc) from exc


@candidate_router.get("/exam-assignments")
def list_candidate_assignments(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    return [
        _assignment_payload(row)
        for row in CandidateRuntimeService.list_assignments(session, _context(principal))
    ]


@candidate_router.get("/exam-assignments/{assignment_id}")
def get_candidate_assignment(
    assignment_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    try:
        assignment = CandidateRuntimeService._assignment(session, assignment_id)
        CandidateRuntimeService._require_candidate(context, assignment.candidate_user_id)
        if context.organisation_id != assignment.organisation_id:
            raise RuntimeError("organisation scope required")
        return _assignment_payload(assignment)
    except Exception as exc:  # noqa: BLE001
        raise _runtime_error(exc) from exc


@candidate_router.post(
    "/exam-assignments/{assignment_id}/attempts", status_code=status.HTTP_201_CREATED
)
def create_attempt(
    assignment_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        attempt = CandidateRuntimeService.create_attempt(
            session, _context(principal), assignment_id
        )
        session.commit()
        return _attempt_payload(attempt)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _runtime_error(exc) from exc


@candidate_router.get("/exam-attempts/{attempt_id}")
def get_attempt(
    attempt_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        return _attempt_payload(
            CandidateRuntimeService._owned_attempt(session, _context(principal), attempt_id)
        )
    except Exception as exc:  # noqa: BLE001
        raise _runtime_error(exc) from exc


@candidate_router.post("/exam-attempts/{attempt_id}/start")
def start_attempt(
    attempt_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        attempt = CandidateRuntimeService.start(session, _context(principal), attempt_id)
        session.commit()
        return CandidateRuntimeService.runtime_state(session, _context(principal), attempt.id)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _runtime_error(exc) from exc


@candidate_router.get("/exam-attempts/{attempt_id}/runtime")
def runtime_state(
    attempt_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        return CandidateRuntimeService.runtime_state(session, _context(principal), attempt_id)
    except Exception as exc:  # noqa: BLE001
        raise _runtime_error(exc) from exc


@candidate_router.put("/exam-attempts/{attempt_id}/responses/{binding_id}")
def save_response(
    attempt_id: uuid.UUID,
    binding_id: uuid.UUID,
    payload: ResponseSave,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        response = CandidateRuntimeService.save_response(
            session, _context(principal), attempt_id, binding_id, **payload.model_dump()
        )
        session.commit()
        return {
            "response_id": str(response.id),
            "revision": response.current_revision,
            "payload": response.current_payload,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _runtime_error(exc) from exc


@candidate_router.post("/exam-attempts/{attempt_id}/navigate")
def navigate(
    attempt_id: uuid.UUID,
    binding_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        runtime_session = CandidateRuntimeService.navigate(
            session, _context(principal), attempt_id, binding_id
        )
        session.commit()
        return {"current_binding_id": str(runtime_session.current_binding_id)}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _runtime_error(exc) from exc


@candidate_router.post("/exam-attempts/{attempt_id}/heartbeat")
def heartbeat(
    attempt_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        attempt = CandidateRuntimeService.heartbeat(session, _context(principal), attempt_id)
        session.commit()
        return _attempt_payload(attempt) | {"server_now": datetime.now().isoformat()}
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _runtime_error(exc) from exc


@candidate_router.post("/exam-attempts/{attempt_id}/submit")
def submit(
    attempt_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        attempt = CandidateRuntimeService.submit(session, _context(principal), attempt_id)
        session.commit()
        return _attempt_payload(attempt)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _runtime_error(exc) from exc
