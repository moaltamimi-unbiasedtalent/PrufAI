from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.knowledge.storage import ObjectStorage, StorageError
from app.models import CandidateAudioRecordingSession, CandidateMediaAsset
from app.models.enums import CandidateMediaStatus

MAX_AUDIO_BYTES = 25 * 1024 * 1024
SUPPORTED_AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/wav"}


class AudioValidationError(ValueError):
    pass


def validate_audio_bytes(content: bytes, declared_mime: str) -> None:
    if not content or len(content) > MAX_AUDIO_BYTES:
        raise AudioValidationError("audio is empty or exceeds technical size limit")
    if declared_mime not in SUPPORTED_AUDIO_TYPES:
        raise AudioValidationError("unsupported audio type")
    signatures = {
        "audio/ogg": content.startswith(b"OggS"),
        "audio/wav": content.startswith(b"RIFF") and content[8:12] == b"WAVE",
        "audio/mp4": len(content) > 12 and content[4:8] == b"ftyp",
        "audio/webm": content.startswith(b"\x1a\x45\xdf\xa3"),
    }
    if not signatures.get(declared_mime, False):
        raise AudioValidationError("audio bytes do not match declared type")


def safe_audio_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    candidate = re.sub(r"[^A-Za-z0-9._-]", "_", filename.replace("/", "_").replace("\\", "_"))
    return candidate[:255] or None


class SpeakingAudioService:
    @staticmethod
    def checksum(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def finalize(
        session: Session,
        recording: CandidateAudioRecordingSession,
        *,
        content: bytes,
        content_type: str,
        filename: str | None,
        storage: ObjectStorage,
    ) -> CandidateMediaAsset:
        validate_audio_bytes(content, content_type)
        checksum = SpeakingAudioService.checksum(content)
        existing = session.scalar(
            select(CandidateMediaAsset).where(
                CandidateMediaAsset.recording_session_id == recording.id
            )
        )
        if existing is not None:
            if existing.sha256 != checksum:
                raise AudioValidationError("operation id is already bound to different audio")
            return existing
        key = f"candidate-audio/{recording.organisation_id}/{recording.attempt_id}/{uuid.uuid4()}"
        try:
            storage.put(key, content, content_type)
        except StorageError:
            recording.status = CandidateMediaStatus.REJECTED
            raise
        asset = CandidateMediaAsset(
            organisation_id=recording.organisation_id,
            candidate_user_id=recording.candidate_user_id,
            attempt_id=recording.attempt_id,
            exam_version_item_binding_id=recording.exam_version_item_binding_id,
            recording_session_id=recording.id,
            content_type=content_type,
            byte_size=len(content),
            sha256=checksum,
            storage_key=key,
            original_filename=safe_audio_filename(filename),
            status=CandidateMediaStatus.READY,
            created_at=datetime.now(timezone.utc),
        )
        session.add(asset)
        recording.status = CandidateMediaStatus.READY
        session.flush()
        return asset
