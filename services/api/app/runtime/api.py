from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, TenantContext
from app.config import get_settings
from app.db import get_db_session
from app.knowledge.storage import build_object_storage
from app.models import (
    CandidateAudioRecordingSession,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateMediaAsset,
)
from app.runtime.service import CandidateRuntimeService, RuntimeConflict, RuntimeExpired
from app.speaking_audio.service import AudioValidationError, SpeakingAudioService

candidate_router = APIRouter(prefix="/api/v1/candidate", tags=["candidate-runtime"])
organisation_router = APIRouter(prefix="/api/v1/organisations", tags=["candidate-assignments"])


def _context(principal: Principal) -> TenantContext:
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


def _assignment_payload(assignment: CandidateExamAssignment) -> dict[str, Any]:
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


def _attempt_payload(attempt: CandidateExamAttempt) -> dict[str, Any]:
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


@candidate_router.post("/attempts/{attempt_id}/items/{binding_id}/audio-recordings")
def start_audio_recording(
    attempt_id: uuid.UUID,
    binding_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        recording = SpeakingAudioService.start_session(
            session, _context(principal), attempt_id, binding_id
        )
        session.commit()
        return {
            "recording_session_id": str(recording.id),
            "status": recording.status.value,
            "recording_started_at": recording.recording_started_at,
            "recording_expires_at": recording.recording_expires_at,
            "attempt_number": recording.attempt_number,
            "policy": recording.policy_snapshot,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        if isinstance(exc, AudioValidationError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise _runtime_error(exc) from exc


@candidate_router.post("/audio-recordings/{recording_id}/finalize")
async def finalize_audio_recording(
    recording_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        context = _context(principal)
        recording = session.get(CandidateAudioRecordingSession, recording_id)
        if (
            recording is None
            or recording.candidate_user_id != context.user_id
            or recording.organisation_id != context.organisation_id
        ):
            raise PermissionError("recording ownership required")
        if (
            recording.recording_expires_at
            and datetime.now(recording.recording_expires_at.tzinfo) > recording.recording_expires_at
        ):
            raise RuntimeExpired("recording deadline expired")
        from app.speaking_audio.service import SpeakingAudioService

        content = await request.body()
        asset = SpeakingAudioService.finalize(
            session,
            recording,
            content=content,
            content_type=request.headers.get("content-type", ""),
            filename=request.headers.get("x-filename"),
            storage=build_object_storage(get_settings()),
        )
        session.commit()
        return {
            "media_asset_id": str(asset.id),
            "status": asset.status.value,
            "sha256": asset.sha256,
            "byte_size": asset.byte_size,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise _runtime_error(exc) from exc


@candidate_router.get("/audio-assets/{asset_id}/stream")
def stream_audio_asset(
    asset_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> Response:
    context = _context(principal)
    asset = session.get(CandidateMediaAsset, asset_id)
    if (
        asset is None
        or asset.status.value != "READY"
        or asset.organisation_id != context.organisation_id
        or asset.candidate_user_id != context.user_id
    ):
        raise HTTPException(status_code=404, detail="audio asset not found")
    content = build_object_storage(get_settings()).get(asset.storage_key)
    range_header = request.headers.get("range")
    start, end = 0, len(content) - 1
    if range_header:
        try:
            value = range_header.removeprefix("bytes=").split("-", 1)
            start = int(value[0])
            end = int(value[1]) if value[1] else end
            if start < 0 or end < start or end >= len(content):
                raise ValueError
        except ValueError as exc:
            raise HTTPException(status_code=416, detail="invalid byte range") from exc
    body = content[start : end + 1]
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(len(body))}
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{len(content)}"
    return Response(
        content=body,
        status_code=206 if range_header else 200,
        media_type=asset.content_type,
        headers=headers,
    )


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
