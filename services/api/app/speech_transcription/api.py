from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.api import Principal, get_principal
from app.authorization import AuthorizationError, TenantContext
from app.config import Settings, get_settings
from app.db import get_db_session
from app.models import (
    AIModelProfile,
    CandidateMediaAsset,
    SpeechTranscription,
    SpeechTranscriptionJob,
    SpeechTranscriptionVersion,
)
from app.models.enums import AIArtifactStatus
from app.speech_transcription.execution import (
    ACTIVE_STATES,
    ExecutionError,
    TranscriptionExecutionService,
    snapshot_profile,
)
from app.speech_transcription.runtime import validate_profile_configuration

router = APIRouter(prefix="/api/v1/organisations", tags=["speech-transcription"])


class TranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: uuid.UUID
    language: str = Field(min_length=2, max_length=20)
    operation_id: uuid.UUID
    candidate_response_revision_id: uuid.UUID
    audio_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def _context(principal: Principal) -> TenantContext:
    if principal.tenant_context is None:
        raise HTTPException(status_code=403, detail="organisation context required")
    return principal.tenant_context


def _asset(
    session: Session,
    principal: Principal,
    organisation_id: uuid.UUID,
    asset_id: uuid.UUID,
    permission: str,
) -> CandidateMediaAsset:
    asset = session.get(CandidateMediaAsset, asset_id)
    if asset is None or asset.organisation_id != organisation_id:
        raise HTTPException(status_code=404, detail="candidate media asset not found")
    try:
        TranscriptionExecutionService.authorize(
            session,
            _context(principal),
            organisation_id,
            permission,
            candidate_user_id=asset.candidate_user_id,
        )
    except AuthorizationError as exc:
        raise HTTPException(
            status_code=403, detail="institution transcription permission required"
        ) from exc
    return asset


def _job(
    session: Session,
    principal: Principal,
    organisation_id: uuid.UUID,
    job_id: uuid.UUID,
    permission: str,
    *,
    lock: bool = False,
) -> SpeechTranscriptionJob:
    statement = select(SpeechTranscriptionJob).where(
        SpeechTranscriptionJob.id == job_id,
        SpeechTranscriptionJob.organisation_id == organisation_id,
    )
    if lock:
        statement = statement.with_for_update()
    job = session.scalar(statement)
    if job is None:
        raise HTTPException(status_code=404, detail="transcription job not found")
    _asset(session, principal, organisation_id, job.candidate_media_asset_id, permission)
    return job


def _version_payload(version: SpeechTranscriptionVersion) -> dict[str, Any]:
    provenance = version.provenance or {}
    return {
        "id": str(version.id),
        "transcription_id": str(version.transcription_id),
        "version_number": version.version_number,
        "candidate_media_asset_id": str(version.candidate_media_asset_id),
        "audio_sha256": version.audio_sha256,
        "profile_id": str(version.profile_id) if version.profile_id else None,
        "profile_version": version.profile_version,
        "execution_job_id": str(version.execution_job_id) if version.execution_job_id else None,
        "candidate_response_revision_id": provenance.get("source", {}).get(
            "candidate_response_revision_id"
        ),
        "test_only": provenance.get("test_only"),
        "evidence_type": "DERIVED_TRANSCRIPT",
        "provider": version.provider,
        "model_name": version.model_name,
        "adapter_version": version.adapter_version,
        "language": version.language or version.transcription.language,
        "transcript": version.transcript,
        "segments": version.segments,
        "checksum": version.checksum,
        "status": version.status.value,
        "retry_count": version.retry_count,
        "created_at": version.created_at.isoformat(),
        "supersedes_version_id": str(version.supersedes_version_id)
        if version.supersedes_version_id
        else None,
    }


@router.get("/{organisation_id}/candidate-media/{asset_id}/transcriptions/options")
def transcription_options(
    organisation_id: uuid.UUID,
    asset_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    from app.speech_transcription.profiles import approved_profile_record

    _asset(session, principal, organisation_id, asset_id, "speech_transcription.request")
    try:
        _, _, _, source = TranscriptionExecutionService.source(session, asset_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="eligible submitted audio evidence required"
        ) from exc
    profiles: list[dict[str, Any]] = []
    for profile in session.scalars(
        select(AIModelProfile)
        .where(
            AIModelProfile.purpose == "SPEECH_TRANSCRIPTION",
            AIModelProfile.status == AIArtifactStatus.ACTIVE,
        )
        .order_by(AIModelProfile.code)
    ):
        configuration = profile.configuration or {}
        languages = configuration.get("languages", [])
        if not isinstance(languages, list) or not all(
            isinstance(value, str) for value in languages
        ):
            languages = []
        try:
            snapshot = snapshot_profile(profile, languages[0] if languages else "de", source)
            availability = validate_profile_configuration(snapshot, settings)
            if approved_profile_record(session, profile, snapshot) is None:
                availability = {
                    "availability": "UNCONFIGURED",
                    "unavailable_reason": "PROFILE_APPROVAL_INVALID",
                    "test_only": profile.provider == "DETERMINISTIC_TEST",
                }
        except ValueError:
            snapshot = {"profile_version": "", "adapter_version": ""}
            availability = {
                "availability": "UNCONFIGURED",
                "unavailable_reason": "INVALID_PROFILE_CONFIGURATION",
                "test_only": profile.provider == "DETERMINISTIC_TEST",
            }
        profiles.append(
            {
                "id": str(profile.id),
                "name": profile.name,
                "version": snapshot["profile_version"],
                "provider": profile.provider,
                "model_name": profile.model_name,
                "adapter_version": snapshot["adapter_version"],
                "languages": languages,
                **availability,
            }
        )
    return {
        "source": {
            "candidate_response_revision_id": source["candidate_response_revision_id"],
            "candidate_media_asset_id": source["candidate_media_asset_id"],
            "audio_sha256": source["audio_sha256"],
            "verified_duration_ms": source["duration_ms"],
        },
        "profiles": profiles,
    }


@router.get("/{organisation_id}/candidate-media/{asset_id}/transcriptions")
def asset_transcriptions(
    organisation_id: uuid.UUID,
    asset_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    _asset(session, principal, organisation_id, asset_id, "speech_transcription.read")
    root = session.scalar(
        select(SpeechTranscription).where(SpeechTranscription.asset_id == asset_id)
    )
    jobs = list(
        session.scalars(
            select(SpeechTranscriptionJob)
            .where(SpeechTranscriptionJob.candidate_media_asset_id == asset_id)
            .order_by(SpeechTranscriptionJob.created_at.desc())
        )
    )
    return {
        "transcription_id": str(root.id) if root else None,
        "asset_id": str(asset_id),
        "jobs": [TranscriptionExecutionService.payload(session, job) for job in jobs],
        "versions": [
            {
                "id": str(version.id),
                "version_number": version.version_number,
                "status": version.status.value,
                "checksum": version.checksum,
            }
            for version in sorted(root.versions, key=lambda item: item.version_number)
        ]
        if root
        else [],
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
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    _asset(session, principal, organisation_id, asset_id, "speech_transcription.request")
    try:
        job = TranscriptionExecutionService.request(
            session,
            context=_context(principal),
            asset_id=asset_id,
            profile_id=payload.profile_id,
            language=payload.language,
            operation_id=payload.operation_id,
            settings=settings,
            response_revision_id=payload.candidate_response_revision_id,
            audio_sha256=payload.audio_sha256,
        )
        session.commit()
        return TranscriptionExecutionService.payload(session, job)
    except AuthorizationError as exc:
        session.rollback()
        raise HTTPException(
            status_code=403, detail="institution transcription permission required"
        ) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="TRANSCRIPTION_OPERATION_CONFLICT") from exc
    except ValueError as exc:
        session.rollback()
        detail = (
            exc.category if isinstance(exc, ExecutionError) else "TRANSCRIPTION_REQUEST_REJECTED"
        )
        raise HTTPException(status_code=409, detail=detail) from exc


@router.get("/{organisation_id}/speech-transcription-jobs/{job_id}")
def get_job(
    organisation_id: uuid.UUID,
    job_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return TranscriptionExecutionService.payload(
        session, _job(session, principal, organisation_id, job_id, "speech_transcription.read")
    )


@router.post("/{organisation_id}/speech-transcription-jobs/{job_id}/retry")
def retry_job(
    organisation_id: uuid.UUID,
    job_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    job = _job(
        session, principal, organisation_id, job_id, "speech_transcription.request", lock=True
    )
    try:
        TranscriptionExecutionService.retry(session, job=job, context=_context(principal))
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="TRANSCRIPTION_RETRY_NOT_ALLOWED") from exc
    return TranscriptionExecutionService.payload(session, job)


@router.post("/{organisation_id}/speech-transcription-jobs/{job_id}/cancel")
def cancel_job(
    organisation_id: uuid.UUID,
    job_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    job = _job(
        session, principal, organisation_id, job_id, "speech_transcription.request", lock=True
    )
    if job.status not in ACTIVE_STATES:
        raise HTTPException(status_code=409, detail="TRANSCRIPTION_CANCEL_NOT_ALLOWED")
    TranscriptionExecutionService.cancel(session, job=job, context=_context(principal))
    session.commit()
    return TranscriptionExecutionService.payload(session, job)


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
    _asset(session, principal, organisation_id, item.asset_id, "speech_transcription.read")
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
    _asset(session, principal, organisation_id, item.asset_id, "speech_transcription.read")
    return [
        _version_payload(version)
        for version in sorted(item.versions, key=lambda value: value.version_number)
    ]


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
    _asset(
        session,
        principal,
        organisation_id,
        version.candidate_media_asset_id,
        "speech_transcription.read",
    )
    return _version_payload(version)
