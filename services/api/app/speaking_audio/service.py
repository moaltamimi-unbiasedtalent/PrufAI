from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import TenantContext
from app.knowledge.storage import ObjectStorage, S3CompatibleObjectStorage, StorageError
from app.models import (
    AuditEvent,
    CandidateAudioRecordingSession,
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponse,
    CandidateResponseMediaBinding,
    CandidateResponseRevision,
    ExamVersionItemBinding,
    QuestionBankItemVersion,
)
from app.models.enums import CandidateAttemptStatus, CandidateMediaStatus, PackResponseMode
from app.question_bank.schemas import AudioRecordingPolicy

MAX_AUDIO_BYTES = 25 * 1024 * 1024
SUPPORTED_AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/wav"}


class AudioValidationError(ValueError):
    pass


class AudioConflict(AudioValidationError):
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
    def durable_storage(storage: ObjectStorage) -> S3CompatibleObjectStorage:
        if not isinstance(storage, S3CompatibleObjectStorage):
            raise StorageError("recorded audio requires durable private S3 storage")
        try:
            storage.client.head_bucket(Bucket=storage.bucket)
        except Exception as exc:
            raise StorageError("private audio bucket is unavailable") from exc
        return storage

    @staticmethod
    def checksum(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def policy(item: dict[str, Any]) -> dict[str, Any]:
        raw = item.get("audio_policy")
        if raw is None and isinstance(item.get("content"), dict):
            raw = item["content"].get("audio_policy")
        try:
            return AudioRecordingPolicy.model_validate(raw).model_dump()
        except ValueError as exc:
            raise AudioValidationError("RUNTIME.AUDIO_POLICY_UNAVAILABLE") from exc

    @staticmethod
    def lock_attempt(
        session: Session, context: TenantContext, attempt_id: uuid.UUID
    ) -> CandidateExamAttempt:
        from app.runtime.service import CandidateRuntimeService

        attempt = session.scalar(
            select(CandidateExamAttempt)
            .where(CandidateExamAttempt.id == attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            attempt is None
            or attempt.candidate_user_id != context.user_id
            or attempt.organisation_id != context.organisation_id
        ):
            raise PermissionError("candidate audio ownership required")
        CandidateRuntimeService.expire_if_due(session, context, attempt)
        return attempt

    @staticmethod
    def identity(
        session: Session, attempt: CandidateExamAttempt, binding_id: uuid.UUID
    ) -> tuple[dict[str, Any], dict[str, str]]:
        from app.runtime.service import CandidateRuntimeService

        item, mode = CandidateRuntimeService._binding_payload(attempt, binding_id)
        binding = session.get(ExamVersionItemBinding, binding_id)
        if mode != PackResponseMode.AUDIO.value or binding is None:
            raise AudioValidationError("item is not a recorded AUDIO task")
        version = session.get(QuestionBankItemVersion, binding.question_bank_item_version_id)
        if (
            version is None
            or str(version.id) != str(item.get("item_version_id"))
            or binding.task.section.module.exam_version_id != attempt.exam_version_id
            or str(item.get("item_version_checksum")) != version.checksum
        ):
            raise AudioConflict("audio item version provenance does not match")
        policy = SpeakingAudioService.policy(item)
        policy_checksum = hashlib.sha256(
            json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return policy, {
            "exam_version_id": str(attempt.exam_version_id),
            "item_version_id": str(version.id),
            "item_version_checksum": version.checksum,
            "policy_checksum": policy_checksum,
        }

    @staticmethod
    def locked_recording(
        session: Session, context: TenantContext, recording_id: uuid.UUID
    ) -> tuple[CandidateExamAttempt, CandidateAudioRecordingSession]:
        candidate = session.get(CandidateAudioRecordingSession, recording_id)
        if (
            candidate is None
            or candidate.candidate_user_id != context.user_id
            or candidate.organisation_id != context.organisation_id
        ):
            raise PermissionError("recording ownership required")
        attempt = SpeakingAudioService.lock_attempt(session, context, candidate.attempt_id)
        recording = session.scalar(
            select(CandidateAudioRecordingSession)
            .where(CandidateAudioRecordingSession.id == recording_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if recording is None:
            raise AudioValidationError("recording is unavailable")
        policy, identity = SpeakingAudioService.identity(
            session, attempt, recording.exam_version_item_binding_id
        )
        if (
            recording.candidate_user_id != attempt.candidate_user_id
            or recording.organisation_id != attempt.organisation_id
            or recording.policy_snapshot != policy
            or recording.evidence_identity != identity
        ):
            raise AudioConflict("recording identity or policy does not match the attempt")
        return attempt, recording

    @staticmethod
    def start_session(
        session: Session,
        context: TenantContext,
        attempt_id: uuid.UUID,
        binding_id: uuid.UUID,
        *,
        operation_id: uuid.UUID,
        storage: ObjectStorage,
    ) -> CandidateAudioRecordingSession:
        SpeakingAudioService.durable_storage(storage)
        attempt = SpeakingAudioService.lock_attempt(session, context, attempt_id)
        policy, identity = SpeakingAudioService.identity(session, attempt, binding_id)
        existing = session.scalar(
            select(CandidateAudioRecordingSession).where(
                CandidateAudioRecordingSession.operation_id == operation_id
            )
        )
        if existing is not None:
            if (
                existing.candidate_user_id != context.user_id
                or existing.organisation_id != context.organisation_id
            ):
                raise PermissionError("recording operation ownership required")
            if (
                existing.attempt_id != attempt_id
                or existing.exam_version_item_binding_id != binding_id
                or existing.evidence_identity != identity
            ):
                raise AudioConflict("recording operation was used for a different request")
            return existing
        if attempt.status is not CandidateAttemptStatus.IN_PROGRESS:
            raise AudioConflict("attempt is not writable")
        previous = session.scalar(
            select(CandidateAudioRecordingSession)
            .where(
                CandidateAudioRecordingSession.attempt_id == attempt.id,
                CandidateAudioRecordingSession.exam_version_item_binding_id == binding_id,
            )
            .order_by(CandidateAudioRecordingSession.attempt_number.desc())
        )
        if previous is not None and previous.status in {
            CandidateMediaStatus.RECORDING,
            CandidateMediaStatus.UPLOADING,
            CandidateMediaStatus.UPLOADED,
            CandidateMediaStatus.VALIDATING,
        }:
            raise AudioConflict("resolve or cancel the existing recording first")
        number = previous.attempt_number + 1 if previous else 1
        if number > int(policy["max_recording_attempts"]):
            raise AudioConflict("audio recording attempt limit reached")
        now = datetime.now(timezone.utc)
        capture_deadline = now + timedelta(seconds=int(policy["max_recording_seconds"]))
        upload_deadline = capture_deadline + timedelta(seconds=int(policy["upload_grace_seconds"]))
        if attempt.deadline_at is not None:
            capture_deadline = min(capture_deadline, attempt.deadline_at)
            upload_deadline = min(upload_deadline, attempt.deadline_at)
        recording_id = uuid.uuid4()
        recording = CandidateAudioRecordingSession(
            id=recording_id,
            attempt_id=attempt.id,
            exam_version_item_binding_id=binding_id,
            candidate_user_id=context.user_id,
            organisation_id=attempt.organisation_id,
            attempt_number=number,
            recording_started_at=now,
            recording_expires_at=capture_deadline,
            upload_expires_at=upload_deadline,
            status=CandidateMediaStatus.RECORDING,
            operation_id=operation_id,
            policy_snapshot=policy,
            evidence_identity=identity,
            storage_key=f"candidate-audio/{attempt.organisation_id}/{attempt.id}/{recording_id}",
        )
        session.add(recording)
        session.flush()
        SpeakingAudioService._audit(
            session, context, "candidate_audio.recording_started", recording
        )
        return recording

    @staticmethod
    def _writable(
        session: Session,
        context: TenantContext,
        attempt: CandidateExamAttempt,
        recording: CandidateAudioRecordingSession,
    ) -> None:
        if recording.status in {
            CandidateMediaStatus.CANCELLED,
            CandidateMediaStatus.REJECTED,
            CandidateMediaStatus.EXPIRED,
        }:
            raise AudioConflict("recording is terminal; start a new governed recording")
        point = datetime.now(timezone.utc)
        deadline = recording.upload_expires_at
        expired = deadline is None or point > (
            deadline if deadline.tzinfo else deadline.replace(tzinfo=timezone.utc)
        )
        if expired or attempt.status is CandidateAttemptStatus.EXPIRED:
            recording.status = CandidateMediaStatus.EXPIRED
            recording.last_error_category = "UPLOAD_DEADLINE_EXPIRED"
            SpeakingAudioService._audit(
                session,
                context,
                "candidate_audio.recording_expired",
                recording,
                reason="recording upload deadline expired",
            )
            session.commit()
            raise AudioConflict("recording upload deadline expired")
        if attempt.status is not CandidateAttemptStatus.IN_PROGRESS:
            raise AudioConflict("attempt is not writable")

    @staticmethod
    def _object_matches(
        storage: S3CompatibleObjectStorage, key: str, digest: str, size: int
    ) -> bool:
        if not storage.exists(key):
            return False
        if storage.size(key) != size or SpeakingAudioService.checksum(storage.get(key)) != digest:
            raise AudioConflict("private object integrity mismatch; object was not overwritten")
        return True

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
        from app.speaking_audio.media import AudioMediaError, validate_recorded_media

        if context is None:
            raise PermissionError("authenticated recording context required")
        backend = SpeakingAudioService.durable_storage(storage)
        attempt, recording = SpeakingAudioService.locked_recording(session, context, recording.id)
        digest = SpeakingAudioService.checksum(content)
        existing = session.scalar(
            select(CandidateMediaAsset).where(
                CandidateMediaAsset.recording_session_id == recording.id
            )
        )
        if existing is not None:
            if existing.sha256 != digest or existing.content_type != content_type:
                raise AudioConflict("recording is already bound to different audio")
            if not SpeakingAudioService._object_matches(
                backend, existing.storage_key, digest, existing.byte_size
            ):
                raise StorageError("finalized audio object is missing")
            return existing
        SpeakingAudioService._writable(session, context, attempt, recording)
        if recording.upload_sha256 is not None and (
            recording.upload_sha256 != digest or recording.upload_content_type != content_type
        ):
            raise AudioConflict("recording upload was already staged with different bytes")
        if recording.upload_sha256 is None:
            try:
                validated = validate_recorded_media(
                    content, content_type, recording.policy_snapshot
                )
            except AudioMediaError as exc:
                recording.last_error_category = str(exc)[:80]
                if str(exc) != "AUDIO.MEDIA_VALIDATOR_UNAVAILABLE":
                    recording.status = CandidateMediaStatus.REJECTED
                    SpeakingAudioService._audit(
                        session,
                        context,
                        "candidate_audio.rejected",
                        recording,
                        reason="media validation failed",
                    )
                session.commit()
                raise
            recording.upload_sha256 = validated.sha256
            recording.upload_byte_size = validated.byte_size
            recording.upload_content_type = content_type
            recording.verified_duration_ms = validated.duration_ms
            recording.validation_provenance = validated.validation_provenance
            recording.upload_started_at = datetime.now(timezone.utc)
            SpeakingAudioService._audit(
                session, context, "candidate_audio.upload_staged", recording
            )
        recording.status = CandidateMediaStatus.UPLOADING
        recording.last_error_category = None
        session.commit()  # Durable receipt exists before the nontransactional object PUT.
        try:
            if not recording.storage_key:
                raise AudioConflict("recording storage identity is unavailable")
            if not SpeakingAudioService._object_matches(
                backend, recording.storage_key, digest, len(content)
            ):
                backend.put(recording.storage_key, content, content_type)
            return SpeakingAudioService.recover(
                session, context, recording.id, storage=backend, filename=filename
            )
        except (StorageError, SQLAlchemyError) as exc:
            session.rollback()
            try:
                _, stage = SpeakingAudioService.locked_recording(session, context, recording.id)
                if stage.status is CandidateMediaStatus.UPLOADING:
                    stage.last_error_category = "UPLOAD_RECOVERY_REQUIRED"
                    session.commit()
            except SQLAlchemyError:
                session.rollback()
            raise StorageError("audio upload needs recovery; retry the same recording") from exc

    @staticmethod
    def recover(
        session: Session,
        context: TenantContext,
        recording_id: uuid.UUID,
        *,
        storage: ObjectStorage,
        filename: str | None = None,
    ) -> CandidateMediaAsset:
        backend = SpeakingAudioService.durable_storage(storage)
        attempt, recording = SpeakingAudioService.locked_recording(session, context, recording_id)
        existing = session.scalar(
            select(CandidateMediaAsset).where(
                CandidateMediaAsset.recording_session_id == recording.id
            )
        )
        if existing is not None:
            if not SpeakingAudioService._object_matches(
                backend, existing.storage_key, existing.sha256, existing.byte_size
            ):
                raise StorageError("finalized audio object is missing")
            return existing
        SpeakingAudioService._writable(session, context, attempt, recording)
        if not all(
            (
                recording.storage_key,
                recording.upload_sha256,
                recording.upload_byte_size,
                recording.upload_content_type,
                recording.verified_duration_ms,
                recording.validation_provenance,
            )
        ):
            raise AudioConflict(
                "no validated upload stage is available; upload the recording first"
            )
        assert recording.storage_key is not None
        assert recording.upload_sha256 is not None
        assert recording.upload_byte_size is not None
        if not SpeakingAudioService._object_matches(
            backend, recording.storage_key, recording.upload_sha256, recording.upload_byte_size
        ):
            raise StorageError(
                "staged object is not available; retry upload with the same recording"
            )
        asset = CandidateMediaAsset(
            id=uuid.uuid5(recording.id, "primary-audio"),
            organisation_id=recording.organisation_id,
            candidate_user_id=recording.candidate_user_id,
            attempt_id=recording.attempt_id,
            exam_version_item_binding_id=recording.exam_version_item_binding_id,
            recording_session_id=recording.id,
            content_type=recording.upload_content_type,
            byte_size=recording.upload_byte_size,
            sha256=recording.upload_sha256,
            duration_ms=recording.verified_duration_ms,
            storage_key=recording.storage_key,
            validation_provenance=dict(recording.validation_provenance),
            original_filename=safe_audio_filename(filename),
            status=CandidateMediaStatus.READY,
            created_at=datetime.now(timezone.utc),
        )
        session.add(asset)
        recording.status = CandidateMediaStatus.READY
        recording.upload_completed_at = datetime.now(timezone.utc)
        recording.last_error_category = None
        session.flush()
        SpeakingAudioService._audit(
            session, context, "candidate_audio.finalized", recording, asset=asset
        )
        session.commit()
        return asset

    @staticmethod
    def cancel(
        session: Session, context: TenantContext, recording_id: uuid.UUID
    ) -> CandidateAudioRecordingSession:
        attempt, recording = SpeakingAudioService.locked_recording(session, context, recording_id)
        if recording.status is CandidateMediaStatus.CANCELLED:
            return recording
        if attempt.status is not CandidateAttemptStatus.IN_PROGRESS:
            raise AudioConflict("attempt is not writable")
        if (
            recording.asset is not None
            and session.scalar(
                select(CandidateResponseMediaBinding.id).where(
                    CandidateResponseMediaBinding.candidate_media_asset_id == recording.asset.id
                )
            )
            is not None
        ):
            raise AudioConflict("attached evidence cannot be cancelled")
        recording.status = CandidateMediaStatus.CANCELLED
        SpeakingAudioService._audit(
            session, context, "candidate_audio.recording_cancelled", recording
        )
        session.flush()
        return recording

    @staticmethod
    def validate_asset_identity(
        session: Session,
        attempt: CandidateExamAttempt,
        binding_id: uuid.UUID,
        asset: CandidateMediaAsset,
    ) -> None:
        policy, identity = SpeakingAudioService.identity(session, attempt, binding_id)
        recording = session.get(CandidateAudioRecordingSession, asset.recording_session_id)
        if (
            asset.status is not CandidateMediaStatus.READY
            or asset.attempt_id != attempt.id
            or asset.organisation_id != attempt.organisation_id
            or asset.candidate_user_id != attempt.candidate_user_id
            or asset.exam_version_item_binding_id != binding_id
            or recording is None
            or recording.status is not CandidateMediaStatus.READY
            or recording.attempt_id != attempt.id
            or recording.organisation_id != attempt.organisation_id
            or recording.candidate_user_id != attempt.candidate_user_id
            or recording.exam_version_item_binding_id != binding_id
            or recording.evidence_identity != identity
            or recording.policy_snapshot != policy
            or recording.storage_key != asset.storage_key
            or recording.upload_sha256 != asset.sha256
            or recording.upload_byte_size != asset.byte_size
            or recording.verified_duration_ms != asset.duration_ms
        ):
            raise AudioConflict(
                "audio asset does not match the exact owned item and recording identity"
            )

    @staticmethod
    def bind_to_response_revision(
        session: Session, *, revision: CandidateResponseRevision, asset: CandidateMediaAsset
    ) -> CandidateResponseMediaBinding:
        attempt = session.get(CandidateExamAttempt, revision.attempt_id)
        response = session.get(CandidateResponse, revision.candidate_response_id)
        if attempt is None or response is None or response.attempt_id != attempt.id:
            raise AudioConflict("audio response identity is unavailable")
        existing = session.scalar(
            select(CandidateResponseMediaBinding).where(
                CandidateResponseMediaBinding.candidate_response_revision_id == revision.id,
                CandidateResponseMediaBinding.binding_role == "PRIMARY_AUDIO",
            )
        )
        SpeakingAudioService.validate_asset_identity(
            session, attempt, response.exam_version_item_binding_id, asset
        )
        if existing is not None:
            if existing.candidate_media_asset_id != asset.id:
                raise AudioConflict("response revision already has a different audio asset")
            return existing
        if attempt.status is not CandidateAttemptStatus.IN_PROGRESS:
            raise AudioConflict("submitted or expired attempts cannot be replaced")
        if revision.payload != {"media_asset_id": str(asset.id)}:
            raise AudioConflict("response payload does not identify the exact audio asset")
        binding = CandidateResponseMediaBinding(
            candidate_response_revision_id=revision.id,
            candidate_media_asset_id=asset.id,
            binding_role="PRIMARY_AUDIO",
            created_at=datetime.now(timezone.utc),
        )
        session.add(binding)
        session.flush()
        return binding

    @staticmethod
    def assert_submittable(session: Session, attempt: CandidateExamAttempt) -> None:
        latest: dict[uuid.UUID, CandidateAudioRecordingSession] = {}
        for recording in session.scalars(
            select(CandidateAudioRecordingSession)
            .where(CandidateAudioRecordingSession.attempt_id == attempt.id)
            .order_by(CandidateAudioRecordingSession.attempt_number.desc())
        ):
            latest.setdefault(recording.exam_version_item_binding_id, recording)
        for recording in latest.values():
            if recording.status in {
                CandidateMediaStatus.RECORDING,
                CandidateMediaStatus.UPLOADING,
                CandidateMediaStatus.UPLOADED,
                CandidateMediaStatus.VALIDATING,
            }:
                raise AudioConflict("recording or upload must be resolved before submission")
            if recording.status is CandidateMediaStatus.READY and recording.asset is not None:
                if (
                    session.scalar(
                        select(CandidateResponseMediaBinding.id).where(
                            CandidateResponseMediaBinding.candidate_media_asset_id
                            == recording.asset.id
                        )
                    )
                    is None
                ):
                    raise AudioConflict(
                        "uploaded audio must be attached or cancelled before submission"
                    )
        for module in attempt.runtime_plan.get("modules", []):
            for section in module.get("sections", []):
                for task in section.get("tasks", []):
                    if task.get("response_mode") != PackResponseMode.AUDIO.value:
                        continue
                    for item in task.get("items", []):
                        binding_id = uuid.UUID(str(item["binding_id"]))
                        SpeakingAudioService.policy(item)
                        response = session.scalar(
                            select(CandidateResponse).where(
                                CandidateResponse.attempt_id == attempt.id,
                                CandidateResponse.exam_version_item_binding_id == binding_id,
                            )
                        )
                        revision = (
                            max(
                                response.revisions,
                                key=lambda row: row.revision_number,
                                default=None,
                            )
                            if response
                            else None
                        )
                        if response is None or revision is None:
                            raise AudioConflict("a required audio response is not attached")
                        asset = session.get(
                            CandidateMediaAsset,
                            uuid.UUID(str(revision.payload.get("media_asset_id"))),
                        )
                        if asset is None:
                            raise AudioConflict("a required audio response is unavailable")
                        SpeakingAudioService.validate_asset_identity(
                            session, attempt, binding_id, asset
                        )
                        from app.config import get_settings
                        from app.knowledge.storage import build_object_storage

                        storage = SpeakingAudioService.durable_storage(
                            build_object_storage(get_settings())
                        )
                        if not SpeakingAudioService._object_matches(
                            storage, asset.storage_key, asset.sha256, asset.byte_size
                        ):
                            raise StorageError(
                                "required audio object is missing; submission was not committed"
                            )
                        if (
                            session.scalar(
                                select(CandidateResponseMediaBinding.id).where(
                                    CandidateResponseMediaBinding.candidate_response_revision_id
                                    == revision.id,
                                    CandidateResponseMediaBinding.candidate_media_asset_id
                                    == asset.id,
                                )
                            )
                            is None
                        ):
                            raise AudioConflict("a required audio response is not attached")
