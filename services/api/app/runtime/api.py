from __future__ import annotations

import asyncio
import tempfile
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.config import get_settings
from app.db import get_db_session
from app.knowledge.storage import StorageError, build_object_storage
from app.lid_practice import service as lid
from app.models import (
    AssessmentResult,
    CandidateAudioRecordingSession,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponseRevision,
    ExamVersion,
    LidPracticeForm,
    User,
)
from app.runtime.service import CandidateRuntimeService, RuntimeConflict, RuntimeExpired
from app.scoring.service import ScoringService
from app.speaking_audio.api import (
    asset_projection,
    audio_error,
    candidate_audio_router,
    reviewer_assets,
    reviewer_audio_router,
    session_projection,
)
from app.speaking_audio.service import AudioValidationError, SpeakingAudioService

candidate_router = APIRouter(prefix="/api/v1/candidate", tags=["candidate-runtime"])
organisation_router = APIRouter(prefix="/api/v1/organisations", tags=["candidate-assignments"])
candidate_router.include_router(candidate_audio_router)
organisation_router.include_router(reviewer_audio_router)


class RecordingStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: uuid.UUID


def _audio_storage() -> Any:
    try:
        return SpeakingAudioService.durable_storage(build_object_storage(get_settings()))
    except StorageError as exc:
        raise audio_error(exc) from exc


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


class AttemptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundesland: str | None = Field(default=None, min_length=5, max_length=5, strict=True)


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


def _candidate_assignment_payload(
    session: Session, assignment: CandidateExamAssignment
) -> dict[str, Any]:
    payload = _assignment_payload(assignment)
    form = session.get(LidPracticeForm, assignment.exam_version_id)
    if form is not None:
        if not lid.assignment_visible(session, form):
            raise HTTPException(status_code=404, detail="candidate assignment not found")
        payload["lid_practice"] = lid.assignment_details(
            form.bundesland, original=lid.is_original_form(form)
        )
    return payload


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
    if isinstance(exc, (AudioValidationError, StorageError)):
        return audio_error(exc)
    if isinstance(exc, RuntimeConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, RuntimeExpired):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (PermissionError, AuthorizationError)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _parse_audio_range(range_header: str | None, size: int) -> tuple[int, int, bool]:
    if size <= 0:
        raise HTTPException(status_code=416, detail="audio asset is empty")
    if range_header is None:
        return 0, size - 1, False
    if not range_header.startswith("bytes=") or "," in range_header:
        raise HTTPException(status_code=416, detail="invalid byte range")
    value = range_header.removeprefix("bytes=")
    if "-" not in value:
        raise HTTPException(status_code=416, detail="invalid byte range")
    start_text, end_text = value.split("-", 1)
    if any(
        part and (not part.isascii() or not part.isdecimal()) for part in (start_text, end_text)
    ):
        raise HTTPException(status_code=416, detail="invalid byte range")
    try:
        if not start_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise ValueError
            start, end = max(0, size - suffix_length), size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
            if start < 0 or end < start or start >= size:
                raise ValueError
            end = min(end, size - 1)
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="invalid byte range") from exc
    return start, end, True


def _audio_response(
    *,
    asset: CandidateMediaAsset,
    request: Request,
    storage: Any,
    session: Session | None = None,
    context: TenantContext | None = None,
    purpose: str = "playback",
) -> Response:
    if session is not None and context is not None:
        SpeakingAudioService.record_access(session, context, asset, purpose=purpose)
        session.commit()
    try:
        size = storage.size(asset.storage_key)
        if hasattr(asset, "byte_size") and size != asset.byte_size:
            raise StorageError("audio object size does not match finalized evidence")
        start, end, partial = _parse_audio_range(request.headers.get("range"), size)
        body = (
            storage.read_range(asset.storage_key, start, end)
            if partial
            else storage.get(asset.storage_key)
        )
        if (
            not partial
            and hasattr(asset, "sha256")
            and SpeakingAudioService.checksum(body) != asset.sha256
        ):
            raise StorageError("audio object checksum does not match finalized evidence")
    except StorageError as exc:
        raise audio_error(exc) from exc
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(len(body)),
        "Cache-Control": "private, no-store",
        "Vary": "Authorization, X-PrufAI-Organisation-ID",
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return Response(
        content=body,
        status_code=206 if partial else 200,
        media_type=asset.content_type,
        headers=headers,
    )


@candidate_router.post("/attempts/{attempt_id}/items/{binding_id}/audio-recordings")
def start_audio_recording(
    attempt_id: uuid.UUID,
    binding_id: uuid.UUID,
    payload: RecordingStart,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        recording = SpeakingAudioService.start_session(
            session,
            _context(principal),
            attempt_id,
            binding_id,
            operation_id=payload.operation_id,
            storage=_audio_storage(),
        )
        session.commit()
        return session_projection(session, recording)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise audio_error(exc) from exc


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
        limit = min(int(recording.policy_snapshot.get("max_upload_bytes", 0)), 25 * 1024 * 1024)
        if limit <= 0:
            raise AudioValidationError("recording policy is unavailable")
        storage = await run_in_threadpool(_audio_storage)

        async def bounded_body() -> bytes:
            total = 0
            with tempfile.SpooledTemporaryFile(
                max_size=min(limit, 1024 * 1024), mode="w+b"
            ) as spool:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > limit:
                        raise AudioValidationError("audio exceeds the governed upload size")
                    spool.write(chunk)
                spool.seek(0)
                return spool.read(limit + 1)

        content = await asyncio.wait_for(bounded_body(), timeout=30)
        asset = await run_in_threadpool(
            SpeakingAudioService.finalize,
            session,
            recording,
            content=content,
            content_type=request.headers.get("content-type", ""),
            filename=request.headers.get("x-filename"),
            storage=storage,
            context=context,
        )
        session.commit()
        return asset_projection(session, asset)
    except asyncio.TimeoutError as exc:
        session.rollback()
        raise HTTPException(
            status_code=408, detail="audio upload timed out; retry the same recording"
        ) from exc
    except Exception as exc:
        session.rollback()
        raise audio_error(exc) from exc


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
    return _audio_response(
        asset=asset,
        request=request,
        storage=_audio_storage(),
        session=session,
        context=context,
        purpose="candidate_playback",
    )


@organisation_router.get(
    "/{organisation_id}/candidate-attempts/{attempt_id}/items/{binding_id}/audio-assets/{asset_id}/stream"
)
def stream_reviewer_audio_asset(
    organisation_id: uuid.UUID,
    attempt_id: uuid.UUID,
    binding_id: uuid.UUID,
    asset_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> Response:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        assets = reviewer_assets(session, context, organisation_id, asset_id)
    except Exception as exc:
        raise audio_error(exc) from exc
    asset = assets[0] if assets else None
    if (
        asset is None
        or asset.attempt_id != attempt_id
        or asset.exam_version_item_binding_id != binding_id
    ):
        raise HTTPException(status_code=404, detail="audio asset not found")
    return _audio_response(
        asset=asset,
        request=request,
        storage=_audio_storage(),
        session=session,
        context=context,
        purpose="reviewer_playback",
    )


@organisation_router.get("/{organisation_id}/exam-assignment-options")
def assignment_options(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    if context.organisation_id != organisation_id:
        raise HTTPException(status_code=403, detail="organisation scope mismatch")
    try:
        return CandidateRuntimeService.assignment_options(session, context)
    except Exception as exc:  # noqa: BLE001
        raise _runtime_error(exc) from exc


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

    try:
        AuthorizationService.require_permission(context, "exam_assignment.read", organisation_id)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    rows = session.scalars(
        select(CandidateExamAssignment)
        .where(CandidateExamAssignment.organisation_id == organisation_id)
        .order_by(CandidateExamAssignment.created_at.desc())
    )
    payloads = []
    can_read_results = AuthorizationService.has_permission(context, "result.read", organisation_id)
    for assignment in rows:
        payload = _assignment_payload(assignment)
        candidate = session.get(User, assignment.candidate_user_id)
        exam_version = session.get(ExamVersion, assignment.exam_version_id)
        payload.update(
            {
                "candidate_user_id": str(assignment.candidate_user_id),
                "candidate_name": candidate.display_name if candidate else None,
                "exam_version_id": str(assignment.exam_version_id),
                "exam_name": exam_version.exam.name if exam_version else None,
                "exam_version_label": exam_version.version_label if exam_version else None,
            }
        )
        for attempt_payload in payload["attempts"]:
            attempt_payload["result"] = None
            if not can_read_results:
                continue
            if attempt_payload["status"] == "SUBMITTED":
                try:
                    attempt_payload["scoring_readiness"] = ScoringService.scoring_readiness(
                        session, context, uuid.UUID(attempt_payload["id"])
                    )
                except AuthorizationError as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
            result = session.scalar(
                select(AssessmentResult).where(
                    AssessmentResult.attempt_id == uuid.UUID(attempt_payload["id"]),
                    AssessmentResult.organisation_id == organisation_id,
                )
            )
            version = (
                max(result.versions, key=lambda item: item.version_number, default=None)
                if result
                else None
            )
            if result is not None and version is not None:
                attempt_payload["result"] = {
                    "id": str(result.id),
                    "version_id": str(version.id),
                    "version_number": version.version_number,
                    "scoring_run_id": str(version.scoring_run_id),
                    "review": ScoringService.review_projection(session, version),
                    "status": version.status.value,
                    "outcome": version.outcome.value if version.outcome else None,
                    "raw_awarded_points": str(version.raw_awarded_points)
                    if version.raw_awarded_points is not None
                    else None,
                    "raw_maximum_points": str(version.raw_maximum_points),
                    "checksum": version.checksum,
                }
        payloads.append(payload)
    return payloads


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
        _candidate_assignment_payload(session, row)
        for row in CandidateRuntimeService.list_assignments(session, _context(principal))
        if lid.assignment_visible(session, session.get(LidPracticeForm, row.exam_version_id))
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
        return _candidate_assignment_payload(session, assignment)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _runtime_error(exc) from exc


@candidate_router.post(
    "/exam-assignments/{assignment_id}/attempts", status_code=status.HTTP_201_CREATED
)
def create_attempt(
    assignment_id: uuid.UUID,
    payload: AttemptCreate | None = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        attempt = CandidateRuntimeService.create_attempt(
            session,
            _context(principal),
            assignment_id,
            bundesland=payload.bundesland if payload is not None else None,
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


@candidate_router.get("/exam-attempts/{attempt_id}/lid-practice-result")
def get_lid_practice_result(
    attempt_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        attempt = CandidateRuntimeService._owned_attempt(session, _context(principal), attempt_id)
        projection = lid.practice_result(session, attempt)
    except Exception as exc:  # noqa: BLE001
        raise _runtime_error(exc) from exc
    if projection is None:
        raise HTTPException(status_code=404, detail="LID_PRACTICE_RESULT_PENDING")
    return projection


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
        revision = session.scalar(
            select(CandidateResponseRevision).where(
                CandidateResponseRevision.attempt_id == attempt_id,
                CandidateResponseRevision.client_operation_id == payload.client_operation_id,
            )
        )
        if revision is None:
            raise RuntimeError("response acknowledgement is unavailable")
        result = {
            "response_id": str(response.id),
            "revision": revision.revision_number,
            "payload": revision.payload,
        }
        if "media_asset_id" in revision.payload:
            result.update(
                {
                    "attachment_status": "ATTACHED",
                    "media_asset_id": revision.payload["media_asset_id"],
                    "candidate_response_revision_id": str(revision.id),
                }
            )
        return result
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
