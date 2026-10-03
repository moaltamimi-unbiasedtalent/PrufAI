from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, AuthorizationService, TenantContext
from app.db import get_db_session
from app.models import CandidateMediaAsset, SpeechTranscription, SpeechTranscriptionVersion
from app.speech_transcription.service import SpeechTranscriptionService

router = APIRouter(prefix="/api/v1/organisations", tags=["speech-transcription"])


class TranscriptionRequest(BaseModel):
    profile_id: uuid.UUID
    language: str = Field(min_length=2, max_length=20)


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


def _authorize_read(principal: Principal, organisation_id: uuid.UUID) -> None:
    context = _context(principal)
    try:
        AuthorizationService.require_permission(
            context, "speech_transcription.read", organisation_id
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _version_payload(version: SpeechTranscriptionVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "transcription_id": str(version.transcription_id),
        "version_number": version.version_number,
        "candidate_media_asset_id": str(version.candidate_media_asset_id),
        "audio_sha256": version.audio_sha256,
        "profile_id": str(version.profile_id) if version.profile_id else None,
        "provider": version.provider,
        "model_name": version.model_name,
        "adapter_version": version.adapter_version,
        "language": version.language or version.transcription.language,
        "transcript": version.transcript,
        "segments": version.segments,
        "checksum": version.checksum,
        "status": version.status.value,
        "retry_count": version.retry_count,
        "supersedes_version_id": (
            str(version.supersedes_version_id) if version.supersedes_version_id else None
        ),
    }


@router.post(
    "/{organisation_id}/candidate-media/{asset_id}/transcriptions",
    status_code=status.HTTP_201_CREATED,
)
def request_transcription(
    organisation_id: uuid.UUID,
    asset_id: uuid.UUID,
    payload: TranscriptionRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    context = _context(principal)
    asset = session.get(CandidateMediaAsset, asset_id)
    if asset is None or asset.organisation_id != organisation_id:
        raise HTTPException(status_code=404, detail="candidate media asset not found")
    try:
        item = SpeechTranscriptionService.request(
            session,
            asset_id=asset_id,
            profile_id=payload.profile_id,
            language=payload.language,
            context=context,
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": str(item.id), "asset_id": str(item.asset_id), "status": item.status.value}


@router.get("/{organisation_id}/speech-transcriptions/{transcription_id}")
def get_transcription(
    organisation_id: uuid.UUID,
    transcription_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    item = session.get(SpeechTranscription, transcription_id)
    if item is None:
        raise HTTPException(status_code=404, detail="transcription not found")
    asset = session.get(CandidateMediaAsset, item.asset_id)
    if asset is None or asset.organisation_id != organisation_id:
        raise HTTPException(status_code=404, detail="transcription not found")
    _authorize_read(principal, organisation_id)
    return {
        "id": str(item.id),
        "asset_id": str(item.asset_id),
        "language": item.language,
        "status": item.status.value,
        "failure_category": item.failure_category,
        "retry_count": item.retry_count,
        "versions": [
            {
                "id": str(version.id),
                "version_number": version.version_number,
                "status": version.status.value,
                "checksum": version.checksum,
            }
            for version in sorted(item.versions, key=lambda value: value.version_number)
        ],
    }


@router.get("/{organisation_id}/speech-transcriptions/{transcription_id}/versions")
def list_transcription_versions(
    organisation_id: uuid.UUID,
    transcription_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> list[dict[str, Any]]:
    item = session.get(SpeechTranscription, transcription_id)
    if item is None:
        raise HTTPException(status_code=404, detail="transcription not found")
    asset = session.get(CandidateMediaAsset, item.asset_id)
    if asset is None or asset.organisation_id != organisation_id:
        raise HTTPException(status_code=404, detail="transcription not found")
    _authorize_read(principal, organisation_id)
    versions = sorted(item.versions, key=lambda value: value.version_number)
    return [_version_payload(version) for version in versions]


@router.get("/{organisation_id}/speech-transcriptions/{transcription_id}/versions/{version_id}")
def get_transcription_version(
    organisation_id: uuid.UUID,
    transcription_id: uuid.UUID,
    version_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    version = session.scalar(
        select(SpeechTranscriptionVersion).where(
            SpeechTranscriptionVersion.id == version_id,
            SpeechTranscriptionVersion.transcription_id == transcription_id,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="transcription version not found")
    asset = session.get(CandidateMediaAsset, version.candidate_media_asset_id)
    if asset is None or asset.organisation_id != organisation_id:
        raise HTTPException(status_code=404, detail="transcription version not found")
    _authorize_read(principal, organisation_id)
    return _version_payload(version)
