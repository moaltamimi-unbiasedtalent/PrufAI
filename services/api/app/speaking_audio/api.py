"""Authenticated metadata and recovery for primary audio; no assessment decisions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.config import get_settings
from app.db import get_db_session
from app.knowledge.storage import StorageError, build_object_storage
from app.models import (
    CandidateAudioRecordingSession,
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponse,
    CandidateResponseMediaBinding,
    CandidateResponseRevision,
)
from app.models.enums import CandidateAttemptStatus, CandidateMediaStatus
from app.speaking_audio.service import AudioConflict, SpeakingAudioService

candidate_audio_router = APIRouter()
reviewer_audio_router = APIRouter()


def context_for(principal: Principal) -> TenantContext:
    if principal.tenant_context is None or principal.tenant_context.organisation_id is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


def audio_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, (PermissionError, AuthorizationError)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, AudioConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (StorageError, SQLAlchemyError)):
        return HTTPException(
            status_code=503, detail="private audio storage or upload recovery is unavailable"
        )
    if "MEDIA_VALIDATOR_UNAVAILABLE" in str(exc):
        return HTTPException(status_code=503, detail="audio media validation is unavailable")
    return HTTPException(status_code=400, detail=str(exc))


def utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (
        value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    ).isoformat()


def asset_projection(session: Session, asset: CandidateMediaAsset) -> dict[str, Any]:
    revision = session.scalar(
        select(CandidateResponseRevision)
        .join(
            CandidateResponseMediaBinding,
            CandidateResponseMediaBinding.candidate_response_revision_id
            == CandidateResponseRevision.id,
        )
        .where(CandidateResponseMediaBinding.candidate_media_asset_id == asset.id)
        .order_by(CandidateResponseRevision.revision_number.desc())
        .limit(1)
    )
    recording = session.get(CandidateAudioRecordingSession, asset.recording_session_id)
    return {
        "media_asset_id": str(asset.id),
        "status": asset.status.value,
        "sha256": asset.sha256,
        "byte_size": asset.byte_size,
        "content_type": asset.content_type,
        "duration_ms": asset.duration_ms,
        "validation_provenance": asset.validation_provenance,
        "recording_session_id": str(asset.recording_session_id),
        "attempt_id": str(asset.attempt_id),
        "binding_id": str(asset.exam_version_item_binding_id),
        "exam_version_id": recording.evidence_identity.get("exam_version_id")
        if recording
        else None,
        "item_version_id": recording.evidence_identity.get("item_version_id")
        if recording
        else None,
        "saved_response_revision": revision.revision_number if revision else None,
        "candidate_response_revision_id": str(revision.id) if revision else None,
        "attachment_status": "ATTACHED" if revision else "UPLOADED_NOT_ATTACHED",
    }


def session_projection(
    session: Session, recording: CandidateAudioRecordingSession
) -> dict[str, Any]:
    asset = session.scalar(
        select(CandidateMediaAsset).where(CandidateMediaAsset.recording_session_id == recording.id)
    )
    now = datetime.now(timezone.utc)

    def remaining(deadline: datetime | None, maximum_ms: int) -> int:
        if deadline is None:
            return 0
        expiry = deadline if deadline.tzinfo else deadline.replace(tzinfo=timezone.utc)
        return min(maximum_ms, max(0, int((expiry - now).total_seconds() * 1000)))

    duration_ms = int(recording.policy_snapshot.get("max_recording_seconds", 0)) * 1000
    grace_ms = int(recording.policy_snapshot.get("upload_grace_seconds", 0)) * 1000
    recording_remaining = (
        remaining(recording.recording_expires_at, duration_ms)
        if recording.status == CandidateMediaStatus.RECORDING
        else 0
    )
    upload_remaining = (
        remaining(recording.upload_expires_at, duration_ms + grace_ms)
        if recording.status in {CandidateMediaStatus.RECORDING, CandidateMediaStatus.UPLOADING}
        else 0
    )
    return {
        "recording_session_id": str(recording.id),
        "status": recording.status.value,
        "attempt_number": recording.attempt_number,
        "recording_started_at": utc(recording.recording_started_at),
        "recording_expires_at": utc(recording.recording_expires_at),
        "upload_expires_at": utc(recording.upload_expires_at),
        "server_now": utc(now),
        "recording_remaining_ms": recording_remaining,
        "upload_remaining_ms": upload_remaining,
        "policy": recording.policy_snapshot,
        "recoverable": recording.upload_sha256 is not None
        and asset is None
        and recording.status == CandidateMediaStatus.UPLOADING,
        "staged_upload": {
            "sha256": recording.upload_sha256,
            "byte_size": recording.upload_byte_size,
            "duration_ms": recording.verified_duration_ms,
            "object_status": "NOT_VERIFIED",
        }
        if recording.upload_sha256
        else None,
        "last_error_category": recording.last_error_category,
        "asset": asset_projection(session, asset) if asset else None,
    }


@candidate_audio_router.get("/attempts/{attempt_id}/items/{binding_id}/audio-evidence")
def candidate_audio_evidence(
    attempt_id: uuid.UUID,
    binding_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        from app.runtime.service import CandidateRuntimeService

        context = context_for(principal)
        attempt = CandidateRuntimeService._owned_attempt(session, context, attempt_id)
        policy, _identity = SpeakingAudioService.identity(session, attempt, binding_id)
        recordings = session.scalars(
            select(CandidateAudioRecordingSession)
            .where(
                CandidateAudioRecordingSession.attempt_id == attempt.id,
                CandidateAudioRecordingSession.exam_version_item_binding_id == binding_id,
            )
            .order_by(CandidateAudioRecordingSession.attempt_number)
        ).all()
        return {
            "policy": policy,
            "recordings": [session_projection(session, row) for row in recordings],
        }
    except Exception as exc:
        raise audio_error(exc) from exc


@candidate_audio_router.post("/audio-recordings/{recording_id}/cancel")
def cancel_audio(
    recording_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        recording = SpeakingAudioService.cancel(session, context_for(principal), recording_id)
        session.commit()
        return session_projection(session, recording)
    except Exception as exc:
        session.rollback()
        raise audio_error(exc) from exc


@candidate_audio_router.post("/audio-recordings/{recording_id}/recover")
def recover_audio(
    recording_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        SpeakingAudioService.recover(
            session,
            context_for(principal),
            recording_id,
            storage=build_object_storage(get_settings()),
        )
        recording = session.get(CandidateAudioRecordingSession, recording_id)
        if recording is None:
            raise HTTPException(status_code=404, detail="recording not found")
        return session_projection(session, recording)
    except Exception as exc:
        session.rollback()
        raise audio_error(exc) from exc


def reviewer_assets(
    session: Session,
    context: TenantContext,
    organisation_id: uuid.UUID,
    asset_id: uuid.UUID | None = None,
) -> list[CandidateMediaAsset]:
    if context.organisation_id != organisation_id:
        raise PermissionError("organisation scope mismatch")
    AuthorizationService.require_permission(context, "subjective.read", organisation_id)
    query = (
        select(CandidateMediaAsset)
        .join(CandidateExamAttempt, CandidateExamAttempt.id == CandidateMediaAsset.attempt_id)
        .join(
            CandidateResponseMediaBinding,
            CandidateResponseMediaBinding.candidate_media_asset_id == CandidateMediaAsset.id,
        )
        .join(
            CandidateResponseRevision,
            CandidateResponseRevision.id
            == CandidateResponseMediaBinding.candidate_response_revision_id,
        )
        .join(
            CandidateResponse,
            CandidateResponse.id == CandidateResponseRevision.candidate_response_id,
        )
        .where(
            CandidateMediaAsset.organisation_id == organisation_id,
            CandidateMediaAsset.candidate_user_id != context.user_id,
            CandidateMediaAsset.status == CandidateMediaStatus.READY,
            CandidateExamAttempt.status == CandidateAttemptStatus.SUBMITTED,
            CandidateResponseRevision.attempt_id == CandidateMediaAsset.attempt_id,
            CandidateResponse.exam_version_item_binding_id
            == CandidateMediaAsset.exam_version_item_binding_id,
            CandidateResponse.current_revision == CandidateResponseRevision.revision_number,
        )
    )
    if asset_id is not None:
        query = query.where(CandidateMediaAsset.id == asset_id)
    return list(
        session.scalars(query.order_by(CandidateMediaAsset.created_at.desc()).limit(100)).unique()
    )


@reviewer_audio_router.get("/{organisation_id}/audio-evidence")
def list_reviewer_audio(
    organisation_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    try:
        return [
            asset_projection(session, row)
            for row in reviewer_assets(session, context_for(principal), organisation_id)
        ]
    except Exception as exc:
        raise audio_error(exc) from exc


@reviewer_audio_router.get("/{organisation_id}/audio-evidence/{asset_id}")
def detail_reviewer_audio(
    organisation_id: uuid.UUID,
    asset_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        assets = reviewer_assets(session, context_for(principal), organisation_id, asset_id)
        if not assets:
            raise HTTPException(status_code=404, detail="audio evidence not found")
        return asset_projection(session, assets[0])
    except Exception as exc:
        raise audio_error(exc) from exc
