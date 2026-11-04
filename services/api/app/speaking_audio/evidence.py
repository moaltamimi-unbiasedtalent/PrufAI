"""Exact original submitted-audio identity shared by governed consumers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CandidateAudioRecordingSession,
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponse,
    CandidateResponseMediaBinding,
    CandidateResponseRevision,
)
from app.models.enums import CandidateAttemptStatus
from app.speaking_audio.service import SpeakingAudioService


class AudioEvidenceError(ValueError):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def resolve_submitted_audio(
    session: Session,
    asset_id: uuid.UUID,
    *,
    response_revision_id: uuid.UUID | None = None,
    audio_sha256: str | None = None,
    lock: bool = False,
) -> tuple[
    CandidateMediaAsset,
    CandidateResponseRevision,
    CandidateResponseMediaBinding,
    dict[str, Any],
]:
    statement = (
        select(CandidateMediaAsset)
        .where(CandidateMediaAsset.id == asset_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    asset = session.scalar(statement)
    if asset is None:
        raise AudioEvidenceError("AUDIO_NOT_FOUND")
    attempt = session.get(CandidateExamAttempt, asset.attempt_id)
    if (
        attempt is None
        or attempt.status is not CandidateAttemptStatus.SUBMITTED
        or attempt.submitted_at is None
    ):
        raise AudioEvidenceError("AUDIO_RESPONSE_NOT_SUBMITTED")
    SpeakingAudioService.validate_asset_identity(
        session, attempt, asset.exam_version_item_binding_id, asset
    )
    SpeakingAudioService._require_complete_evidence_validation(session, asset)
    recording = session.get(CandidateAudioRecordingSession, asset.recording_session_id)
    assert recording is not None
    if (
        isinstance(asset.duration_ms, bool)
        or not isinstance(asset.duration_ms, int)
        or asset.duration_ms <= 0
    ):
        raise AudioEvidenceError("AUDIO_DURATION_UNVERIFIED")
    if not 0 < asset.byte_size <= 25_000_000:
        raise AudioEvidenceError("AUDIO_TOO_LARGE")
    for provenance in (asset.validation_provenance, recording.validation_provenance):
        if (
            provenance.get("validator") != "ffprobe_and_complete_ffmpeg_audio_decode"
            or provenance.get("source_sha256") != asset.sha256
            or provenance.get("source_byte_size") != asset.byte_size
            or provenance.get("duration_ms") != asset.duration_ms
        ):
            raise AudioEvidenceError("AUDIO_VALIDATION_PROVENANCE_MISMATCH")
    response = session.scalar(
        select(CandidateResponse)
        .where(
            CandidateResponse.attempt_id == attempt.id,
            CandidateResponse.exam_version_item_binding_id == asset.exam_version_item_binding_id,
        )
        .execution_options(populate_existing=True)
    )
    if response is None:
        raise AudioEvidenceError("AUDIO_RESPONSE_UNBOUND")
    revision = session.scalar(
        select(CandidateResponseRevision)
        .where(
            CandidateResponseRevision.candidate_response_id == response.id,
            CandidateResponseRevision.revision_number == response.current_revision,
        )
        .execution_options(populate_existing=True)
    )
    if (
        revision is None
        or revision.attempt_id != attempt.id
        or revision.payload != {"media_asset_id": str(asset.id)}
        or response.current_payload != revision.payload
    ):
        raise AudioEvidenceError("AUDIO_RESPONSE_BINDING_MISMATCH")
    binding = session.scalar(
        select(CandidateResponseMediaBinding)
        .where(
            CandidateResponseMediaBinding.candidate_response_revision_id == revision.id,
            CandidateResponseMediaBinding.binding_role == "PRIMARY_AUDIO",
        )
        .execution_options(populate_existing=True)
    )
    if binding is None or binding.candidate_media_asset_id != asset.id:
        raise AudioEvidenceError("AUDIO_RESPONSE_BINDING_MISMATCH")
    if response_revision_id is not None and revision.id != response_revision_id:
        raise AudioEvidenceError("STALE_AUDIO_RESPONSE_REVISION")
    if audio_sha256 is not None and audio_sha256 != asset.sha256:
        raise AudioEvidenceError("STALE_AUDIO_CHECKSUM")
    policy, identity = SpeakingAudioService.identity(
        session, attempt, asset.exam_version_item_binding_id
    )
    source = {
        "organisation_id": str(asset.organisation_id),
        "candidate_user_id": str(asset.candidate_user_id),
        "attempt_id": str(attempt.id),
        "exam_version_item_binding_id": str(asset.exam_version_item_binding_id),
        "candidate_response_revision_id": str(revision.id),
        "candidate_response_media_binding_id": str(binding.id),
        "candidate_media_asset_id": str(asset.id),
        "recording_session_id": str(recording.id),
        "audio_sha256": asset.sha256,
        "byte_size": asset.byte_size,
        "duration_ms": asset.duration_ms,
        "content_type": asset.content_type,
        "recording_policy": policy,
        **identity,
    }
    return asset, revision, binding, source
