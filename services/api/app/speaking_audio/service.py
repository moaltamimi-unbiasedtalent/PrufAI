from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import TenantContext
from app.knowledge.storage import ObjectStorage, StorageError
from app.models import (
    AuditEvent,
    CandidateAudioRecordingSession,
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponseMediaBinding,
    CandidateResponseRevision,
)
from app.models.enums import CandidateAttemptStatus, CandidateMediaStatus, PackResponseMode

MAX_AUDIO_BYTES = 25 * 1024 * 1024
SUPPORTED_AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/wav"}


class AudioValidationError(ValueError):
    pass


def validate_audio_bytes(content: bytes, declared_mime: str) -> None:
    declared_mime = declared_mime.split(";", 1)[0].strip().lower()
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
    def _audit(
        session: Session,
        context: TenantContext | None,
        event_type: str,
        recording: CandidateAudioRecordingSession,
        *,
        asset: CandidateMediaAsset | None = None,
        revision_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> None:
        if context is None:
            return
        metadata: dict[str, object] = {
            "candidate_exam_attempt_id": str(recording.attempt_id),
            "item_binding_id": str(recording.exam_version_item_binding_id),
            "recording_session_id": str(recording.id),
        }
        if asset is not None:
            metadata["candidate_media_asset_id"] = str(asset.id)
            metadata["sha256"] = asset.sha256
        if revision_id is not None:
            metadata["candidate_response_revision_id"] = str(revision_id)
        AuditService.record(
            session,
            context,
            event_type=event_type,
            entity_type="CANDIDATE_AUDIO",
            entity_id=str(asset.id if asset is not None else recording.id),
            organisation_id=recording.organisation_id,
            reason=reason,
            metadata=metadata,
            require_audit_permission=False,
        )

    @staticmethod
    def record_access(
        session: Session,
        context: TenantContext,
        asset: CandidateMediaAsset,
        *,
        purpose: str,
    ) -> None:
        existing = session.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == "candidate_audio.accessed",
                AuditEvent.entity_id == str(asset.id),
                AuditEvent.actor_id == context.user_id,
                AuditEvent.organisation_id == asset.organisation_id,
            )
        )
        if existing is not None:
            return
        recording = session.get(CandidateAudioRecordingSession, asset.recording_session_id)
        if recording is None:
            return
        SpeakingAudioService._audit(
            session,
            context,
            "candidate_audio.accessed",
            recording,
            asset=asset,
            reason=purpose,
        )

    @staticmethod
    def start_session(
        session: Session, context: TenantContext, attempt_id: uuid.UUID, binding_id: uuid.UUID
    ) -> CandidateAudioRecordingSession:
        from app.runtime.service import CandidateRuntimeService

        attempt = CandidateRuntimeService._owned_attempt(session, context, attempt_id)
        CandidateRuntimeService.expire_if_due(session, context, attempt)
        if attempt.status is not CandidateAttemptStatus.IN_PROGRESS:
            raise AudioValidationError("attempt is not writable")
        item, mode = CandidateRuntimeService._binding_payload(attempt, binding_id)
        if mode != PackResponseMode.AUDIO.value:
            raise AudioValidationError("item is not a recorded AUDIO task")
        policy = item.get("audio_policy") or item.get("content", {}).get("audio_policy")
        if not isinstance(policy, dict) or not isinstance(policy.get("max_recording_seconds"), int):
            raise AudioValidationError("RUNTIME.AUDIO_POLICY_INCOMPLETE")
        max_attempts = policy.get("max_recording_attempts")
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise AudioValidationError("RUNTIME.AUDIO_POLICY_INCOMPLETE")
        current = session.scalar(
            select(CandidateAudioRecordingSession)
            .where(
                CandidateAudioRecordingSession.attempt_id == attempt.id,
                CandidateAudioRecordingSession.exam_version_item_binding_id == binding_id,
            )
            .order_by(CandidateAudioRecordingSession.attempt_number.desc())
        )
        attempt_number = (current.attempt_number + 1) if current else 1
        if attempt_number > max_attempts:
            raise AudioValidationError("audio recording attempt limit reached")
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(seconds=policy["max_recording_seconds"])
        if attempt.deadline_at is not None and attempt.deadline_at < deadline:
            deadline = attempt.deadline_at
        recording = CandidateAudioRecordingSession(
            attempt_id=attempt.id,
            exam_version_item_binding_id=binding_id,
            candidate_user_id=attempt.candidate_user_id,
            organisation_id=attempt.organisation_id,
            attempt_number=attempt_number,
            recording_started_at=now,
            recording_expires_at=deadline,
            status=CandidateMediaStatus.RECORDING,
            operation_id=uuid.uuid4(),
            policy_snapshot=policy,
        )
        session.add(recording)
        session.flush()
        SpeakingAudioService._audit(
            session, context, "candidate_audio.recording_started", recording
        )
        return recording

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
        context: TenantContext | None = None,
    ) -> CandidateMediaAsset:
        try:
            validate_audio_bytes(content, content_type)
        except AudioValidationError as exc:
            recording.status = CandidateMediaStatus.REJECTED
            SpeakingAudioService._audit(
                session, context, "candidate_audio.rejected", recording, reason=str(exc)
            )
            session.flush()
            raise
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
            SpeakingAudioService._audit(
                session,
                context,
                "candidate_audio.rejected",
                recording,
                reason="object storage unavailable",
            )
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
        SpeakingAudioService._audit(
            session, context, "candidate_audio.finalized", recording, asset=asset
        )
        return asset

    @staticmethod
    def bind_to_response_revision(
        session: Session,
        *,
        revision: CandidateResponseRevision,
        asset: CandidateMediaAsset,
    ) -> CandidateResponseMediaBinding:
        attempt = session.get(CandidateExamAttempt, revision.attempt_id)
        if attempt is not None and attempt.status is CandidateAttemptStatus.SUBMITTED:
            raise AudioValidationError("submitted attempts cannot be replaced")
        if revision.attempt_id != asset.attempt_id:
            raise AudioValidationError("audio and response attempt do not match")
        existing = session.scalar(
            select(CandidateResponseMediaBinding).where(
                CandidateResponseMediaBinding.candidate_response_revision_id == revision.id,
                CandidateResponseMediaBinding.binding_role == "PRIMARY_AUDIO",
            )
        )
        if existing is not None:
            if existing.candidate_media_asset_id != asset.id:
                raise AudioValidationError("response revision already has a different audio asset")
            return existing
        binding = CandidateResponseMediaBinding(
            candidate_response_revision_id=revision.id,
            candidate_media_asset_id=asset.id,
            binding_role="PRIMARY_AUDIO",
            created_at=datetime.now(timezone.utc),
        )
        session.add(binding)
        session.flush()
        return binding
