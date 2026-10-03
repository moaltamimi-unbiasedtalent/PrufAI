from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationService, TenantContext
from app.knowledge.storage import ObjectStorage
from app.models import (
    AIModelProfile,
    CandidateMediaAsset,
    CandidateResponseMediaBinding,
    SpeechTranscription,
    SpeechTranscriptionVersion,
)
from app.models.enums import AIArtifactStatus, CandidateMediaStatus, TranscriptionStatus
from app.speech_transcription.provider import (
    EmptyProviderResult,
    InvalidProviderResult,
    ProviderError,
    SpeechTranscriptionProvider,
    TranscriptionResult,
)

MAX_SEGMENTS = 2_000
MAX_SEGMENT_TEXT = 20_000
MAX_TRANSCRIPT_TEXT = 200_000
MAX_RETRIES = 3


@dataclass(frozen=True)
class AudioEvidenceReference:
    candidate_media_asset_id: uuid.UUID
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class TranscriptEvidenceReference:
    speech_transcription_version_id: uuid.UUID
    segment_id: str
    start_offset: int | None = None
    end_offset: int | None = None


class SpeechTranscriptionService:
    PURPOSE = "SPEECH_TRANSCRIPTION"
    RETRY_MAX = 3

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _normalise(text: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()

    @staticmethod
    def _configuration_checksum(configuration: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _persistable_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
        """Keep reproducible settings without persisting provider credentials."""
        return {
            key: value
            for key, value in configuration.items()
            if not any(
                marker in key.lower() for marker in ("secret", "password", "token", "api_key")
            )
        }

    @classmethod
    def _version_checksum(
        cls,
        *,
        asset: CandidateMediaAsset,
        profile: AIModelProfile,
        language: str,
        transcript: str,
        segments: list[dict[str, Any]],
        configuration: dict[str, Any],
    ) -> str:
        payload = {
            "asset_id": str(asset.id),
            "audio_sha256": asset.sha256,
            "profile_id": str(profile.id),
            "profile_code": profile.code,
            "provider": profile.provider,
            "model_name": profile.model_name,
            "configuration": configuration,
            "language": language,
            "transcript": transcript,
            "segments": segments,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @classmethod
    def _audit(
        cls,
        session: Session,
        context: TenantContext | None,
        event_type: str,
        transcription: SpeechTranscription,
        *,
        version: SpeechTranscriptionVersion | None = None,
        failure_category: str | None = None,
    ) -> None:
        if context is None:
            return
        metadata: dict[str, Any] = {
            "speech_transcription_id": str(transcription.id),
            "candidate_media_asset_id": str(transcription.asset_id),
            "status": transcription.status.value,
        }
        if version is not None:
            metadata.update(
                {
                    "speech_transcription_version_id": str(version.id),
                    "audio_sha256": version.audio_sha256,
                    "checksum": version.checksum,
                }
            )
        if failure_category is not None:
            metadata["failure_category"] = failure_category
        AuditService.record(
            session,
            context,
            event_type=event_type,
            entity_type="SPEECH_TRANSCRIPTION",
            entity_id=str(transcription.id),
            organisation_id=_asset_org(session, transcription.asset_id),
            metadata=metadata,
            require_audit_permission=False,
        )

    @staticmethod
    def _asset(session: Session, asset_id: uuid.UUID) -> CandidateMediaAsset:
        asset = session.get(CandidateMediaAsset, asset_id)
        if asset is None:
            raise ValueError("candidate media asset not found")
        if asset.status not in (CandidateMediaStatus.READY, CandidateMediaStatus.READY.value):
            raise ValueError("only READY candidate media assets may be transcribed")
        bound = session.scalar(
            select(CandidateResponseMediaBinding.id).where(
                CandidateResponseMediaBinding.candidate_media_asset_id == asset.id
            )
        )
        if bound is None:
            raise ValueError("candidate media asset is not bound to a response revision")
        return asset

    @classmethod
    def _authorize(
        cls, context: TenantContext | None, asset: CandidateMediaAsset, permission: str
    ) -> None:
        if context is None:
            return
        if context.organisation_id is None or context.organisation_id != asset.organisation_id:
            raise PermissionError("organisation context does not own candidate media")
        if context.user_id == asset.candidate_user_id:
            raise PermissionError("candidate transcript workflow access is not enabled")
        AuthorizationService.require_permission(context, permission, asset.organisation_id)

    @classmethod
    def _profile(cls, session: Session, profile_id: uuid.UUID) -> AIModelProfile:
        profile = session.get(AIModelProfile, profile_id)
        if profile is None:
            raise ValueError("transcription profile not found")
        if profile.status not in (AIArtifactStatus.ACTIVE, AIArtifactStatus.ACTIVE.value):
            raise ValueError("transcription profile is not active")
        if profile.purpose != cls.PURPOSE:
            raise ValueError("model profile is not eligible for speech transcription")
        if not profile.provider or not profile.model_name:
            raise ValueError("transcription profile is incomplete")
        return profile

    @classmethod
    def request(
        cls,
        session: Session,
        *,
        asset_id: uuid.UUID,
        profile_id: uuid.UUID,
        language: str,
        context: TenantContext | None = None,
        operation_id: uuid.UUID | None = None,
    ) -> SpeechTranscription:
        asset = cls._asset(session, asset_id)
        cls._authorize(context, asset, "speech_transcription.request")
        cls._profile(session, profile_id)
        predicates = [SpeechTranscription.asset_id == asset.id]
        if operation_id is not None:
            predicates.append(SpeechTranscription.operation_id == operation_id)
        existing = session.scalar(select(SpeechTranscription).where(or_(*predicates)))
        if existing is not None:
            if existing.asset_id != asset.id:
                raise ValueError("operation id is already bound to another media asset")
            return existing
        transcription = SpeechTranscription(
            asset_id=asset.id,
            operation_id=operation_id,
            language=language,
            status=TranscriptionStatus.CREATED,
        )
        session.add(transcription)
        session.flush()
        cls._audit(session, context, "speech_transcription.requested", transcription)
        return transcription

    @classmethod
    def transcribe(
        cls,
        session: Session,
        *,
        asset_id: uuid.UUID,
        profile_id: uuid.UUID,
        storage: ObjectStorage,
        provider: SpeechTranscriptionProvider,
        language: str,
        context: TenantContext | None = None,
        max_retries: int = RETRY_MAX,
        configuration: dict[str, Any] | None = None,
        operation_id: uuid.UUID | None = None,
    ) -> SpeechTranscriptionVersion:
        asset = cls._asset(session, asset_id)
        cls._authorize(context, asset, "speech_transcription.request")
        profile = cls._profile(session, profile_id)
        transcription = cls.request(
            session,
            asset_id=asset_id,
            profile_id=profile_id,
            language=language,
            context=context,
            operation_id=operation_id,
        )
        configuration = dict(configuration or profile.configuration or {})
        persisted_configuration = cls._persistable_configuration(configuration)
        transcription.status = TranscriptionStatus.RUNNING
        transcription.failure_category = None
        session.flush()
        cls._audit(session, context, "speech_transcription.started", transcription)
        attempts = 0
        try:
            audio = storage.get(asset.storage_key)
            if len(audio) != asset.byte_size:
                raise ValueError("stored audio size does not match candidate media asset")
            if hashlib.sha256(audio).hexdigest() != asset.sha256:
                raise ValueError("source audio checksum does not match candidate media asset")
            result: TranscriptionResult | None = None
            while attempts < min(max(max_retries, 1), MAX_RETRIES):
                attempts += 1
                try:
                    result = provider.transcribe(
                        audio,
                        language=language,
                        configuration=configuration,
                        limits={
                            "max_segments": MAX_SEGMENTS,
                            "max_segment_text": MAX_SEGMENT_TEXT,
                            "max_transcript_text": MAX_TRANSCRIPT_TEXT,
                        },
                    )
                    break
                except ProviderError:
                    if attempts >= min(max(max_retries, 1), MAX_RETRIES):
                        raise
            if result is None:
                raise ProviderError("provider returned no result")
            transcript, segments = cls._validate_result(result, asset)
            version_number = (
                session.scalar(
                    select(SpeechTranscriptionVersion.version_number)
                    .where(SpeechTranscriptionVersion.transcription_id == transcription.id)
                    .order_by(SpeechTranscriptionVersion.version_number.desc())
                )
                or 0
            ) + 1
            previous = cls.current_version(session, transcription.id)
            version = SpeechTranscriptionVersion(
                transcription_id=transcription.id,
                version_number=version_number,
                candidate_media_asset_id=asset.id,
                audio_sha256=asset.sha256,
                profile_id=profile.id,
                transcript=transcript,
                segments=segments,
                checksum=cls._version_checksum(
                    asset=asset,
                    profile=profile,
                    language=language,
                    transcript=transcript,
                    segments=segments,
                    configuration=configuration,
                ),
                status=TranscriptionStatus.READY,
                provider=profile.provider,
                model_name=profile.model_name,
                adapter_version=result.adapter_version,
                language=language,
                configuration=persisted_configuration,
                configuration_checksum=cls._configuration_checksum(persisted_configuration),
                retry_count=attempts - 1,
                created_at=cls._now(),
                supersedes_version_id=previous.id if previous is not None else None,
            )
            session.add(version)
            if previous is not None:
                previous.status = TranscriptionStatus.SUPERSEDED
            transcription.status = TranscriptionStatus.READY
            transcription.retry_count = attempts - 1
            session.flush()
            cls._audit(
                session,
                context,
                "speech_transcription.completed",
                transcription,
                version=version,
            )
            if previous is not None:
                cls._audit(
                    session,
                    context,
                    "speech_transcription.version_superseded",
                    transcription,
                    version=previous,
                )
            return version
        except Exception as exc:  # noqa: BLE001
            category = getattr(exc, "category", "CONFIGURATION_ERROR")
            transcription.status = TranscriptionStatus.FAILED
            transcription.failure_category = category
            transcription.retry_count = max(attempts - 1, 0)
            session.flush()
            cls._audit(
                session,
                context,
                "speech_transcription.failed",
                transcription,
                failure_category=category,
            )
            raise

    @classmethod
    def _validate_result(
        cls, result: TranscriptionResult, asset: CandidateMediaAsset
    ) -> tuple[str, list[dict[str, Any]]]:
        transcript = cls._normalise(result.transcript)
        if not transcript:
            raise EmptyProviderResult("provider returned an empty transcript")
        if len(transcript) > MAX_TRANSCRIPT_TEXT or len(result.segments) > MAX_SEGMENTS:
            raise InvalidProviderResult("provider result exceeds technical limits")
        seen: set[str] = set()
        normalized: list[dict[str, Any]] = []
        previous_start = -1
        for segment in result.segments:
            if segment.segment_id in seen:
                raise InvalidProviderResult("duplicate transcription segment id")
            if not isinstance(segment.start_ms, int) or not isinstance(segment.end_ms, int):
                raise InvalidProviderResult("segment timing must be integer milliseconds")
            if segment.start_ms < 0 or segment.end_ms <= segment.start_ms:
                raise InvalidProviderResult("invalid segment timing")
            if asset.duration_ms is not None and segment.end_ms > asset.duration_ms:
                raise InvalidProviderResult("segment exceeds verified audio duration")
            if segment.start_ms < previous_start:
                raise InvalidProviderResult("segments must be deterministically ordered")
            text = cls._normalise(segment.text)
            if not text or len(text) > MAX_SEGMENT_TEXT:
                raise InvalidProviderResult("invalid segment text")
            value = {
                "segment_id": segment.segment_id,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "text": text,
            }
            if segment.confidence is not None:
                if not 0 <= segment.confidence <= 1:
                    raise InvalidProviderResult("invalid provider confidence")
                value["confidence"] = segment.confidence
            normalized.append(value)
            seen.add(segment.segment_id)
            previous_start = segment.start_ms
        return transcript, normalized

    @staticmethod
    def current_version(
        session: Session, transcription_id: uuid.UUID
    ) -> SpeechTranscriptionVersion | None:
        return session.scalar(
            select(SpeechTranscriptionVersion)
            .where(
                SpeechTranscriptionVersion.transcription_id == transcription_id,
                SpeechTranscriptionVersion.status == TranscriptionStatus.READY,
            )
            .order_by(SpeechTranscriptionVersion.version_number.desc())
        )

    @staticmethod
    def get_version(session: Session, version_id: uuid.UUID) -> SpeechTranscriptionVersion:
        version = session.get(SpeechTranscriptionVersion, version_id)
        if version is None:
            raise ValueError("transcription version not found")
        return version

    @classmethod
    def list_versions(
        cls, session: Session, transcription_id: uuid.UUID
    ) -> list[SpeechTranscriptionVersion]:
        return list(
            session.scalars(
                select(SpeechTranscriptionVersion)
                .where(SpeechTranscriptionVersion.transcription_id == transcription_id)
                .order_by(SpeechTranscriptionVersion.version_number)
            )
        )

    @classmethod
    def diff_versions(
        cls, session: Session, left_id: uuid.UUID, right_id: uuid.UUID
    ) -> dict[str, Any]:
        left = cls.get_version(session, left_id)
        right = cls.get_version(session, right_id)
        if left.transcription_id != right.transcription_id:
            raise ValueError("transcription versions must share a transcription root")
        fields = (
            "candidate_media_asset_id",
            "audio_sha256",
            "profile_id",
            "provider",
            "model_name",
            "adapter_version",
            "language",
            "transcript",
            "segments",
            "checksum",
        )
        return {
            "left_version_id": str(left.id),
            "right_version_id": str(right.id),
            "changed": {
                field: {
                    "left": _json_safe(getattr(left, field)),
                    "right": _json_safe(getattr(right, field)),
                }
                for field in fields
                if getattr(left, field) != getattr(right, field)
            },
        }

    @classmethod
    def validate_transcript_reference(
        cls, session: Session, reference: TranscriptEvidenceReference
    ) -> None:
        version = cls.get_version(session, reference.speech_transcription_version_id)
        segment = next(
            (item for item in version.segments if item.get("segment_id") == reference.segment_id),
            None,
        )
        if segment is None:
            raise ValueError("transcript segment does not exist in exact version")
        if reference.start_offset is not None and reference.start_offset < 0:
            raise ValueError("transcript start offset is invalid")
        if reference.end_offset is not None and reference.end_offset > len(segment["text"]):
            raise ValueError("transcript end offset is invalid")
        if (
            reference.start_offset is not None
            and reference.end_offset is not None
            and reference.end_offset <= reference.start_offset
        ):
            raise ValueError("transcript offsets are invalid")

    @staticmethod
    def validate_audio_reference(session: Session, reference: AudioEvidenceReference) -> None:
        asset = session.get(CandidateMediaAsset, reference.candidate_media_asset_id)
        if asset is None or asset.status is not CandidateMediaStatus.READY:
            raise ValueError("audio evidence asset is not ready")
        if reference.start_ms < 0 or reference.end_ms <= reference.start_ms:
            raise ValueError("audio evidence bounds are invalid")
        if asset.duration_ms is not None and reference.end_ms > asset.duration_ms:
            raise ValueError("audio evidence exceeds verified duration")


def _asset_org(session: Session, asset_id: uuid.UUID) -> uuid.UUID | None:
    asset = session.get(CandidateMediaAsset, asset_id)
    return asset.organisation_id if asset is not None else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    return value
