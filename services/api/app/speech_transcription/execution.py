"""PostgreSQL-backed transcription execution, separate from evidence authority."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import unicodedata
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app import pilot
from app.audit import AuditService
from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.authorization.membership import require_active_tenant_permission
from app.config import Settings
from app.knowledge.storage import ObjectStorage, StorageError, StorageIntegrityError
from app.models import (
    AIModelProfile,
    CandidateMediaAsset,
    CandidateResponseMediaBinding,
    CandidateResponseRevision,
    SpeechTranscription,
    SpeechTranscriptionJob,
    SpeechTranscriptionVersion,
)
from app.models.enums import (
    ActorType,
    TranscriptionStatus,
)
from app.operations.telemetry import Failure, Signal, record
from app.speaking_audio.service import SpeakingAudioService
from app.speech_transcription.provider import (
    EmptyProviderResult,
    InvalidProviderResult,
    ProviderError,
    SpeechTranscriptionProvider,
    TranscriptionResult,
)
from app.speech_transcription.service import (
    MAX_SEGMENT_TEXT,
    MAX_SEGMENTS,
    MAX_TRANSCRIPT_TEXT,
    SpeechTranscriptionService,
)

ACTIVE_STATES = ("QUEUED", "RUNNING", "RETRYABLE")
RETRYABLE_FAILURES = {
    "PROVIDER_TIMEOUT",
    "PROVIDER_UNAVAILABLE",
    "OBJECT_STORAGE_UNAVAILABLE",
    "DATABASE_UNAVAILABLE",
    "WORKER_LEASE_EXPIRED",
}
ProviderFactory = Callable[[dict[str, Any], Settings], SpeechTranscriptionProvider]
SessionFactory = Callable[[], Session]


class ExecutionError(ValueError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _checksum(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _safe_configuration(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or (
                key != "audio_transfer_authorization_reference"
                and any(
                    marker in key.lower()
                    for marker in ("secret", "password", "token", "api_key", "authorization")
                )
            ):
                raise ExecutionError("PROFILE_CONTAINS_CREDENTIALS")
            _safe_configuration(child)
    elif isinstance(value, list):
        for child in value:
            _safe_configuration(child)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ExecutionError("INVALID_PROFILE_CONFIGURATION")


def snapshot_profile(
    profile: AIModelProfile, language: str, source: dict[str, Any]
) -> dict[str, Any]:
    """Content-address a registry revision; execution never reloads current settings."""
    configuration = copy.deepcopy(profile.configuration)
    if not isinstance(configuration, dict):
        raise ExecutionError("INVALID_PROFILE_CONFIGURATION")
    _safe_configuration(configuration)
    identity = {
        "profile_id": str(profile.id),
        "profile_code": profile.code,
        "provider": profile.provider,
        "model_name": profile.model_name,
        "adapter_version": str(configuration.get("adapter_version", "1")),
        "configuration": configuration,
    }
    try:
        version = _checksum(identity)
    except (TypeError, ValueError) as exc:
        raise ExecutionError("INVALID_PROFILE_CONFIGURATION") from exc
    return {
        "schema_version": "1",
        **identity,
        "profile_version": version,
        "language": language,
        "source": copy.deepcopy(source),
    }


class TranscriptionExecutionService:
    @staticmethod
    def authorize(
        session: Session,
        context: TenantContext,
        organisation_id: uuid.UUID,
        permission: str,
        *,
        candidate_user_id: uuid.UUID | None = None,
    ) -> TenantContext:
        if context.organisation_id != organisation_id or context.user_id == candidate_user_id:
            raise AuthorizationError("institution evidence authority required")
        # Reload actual role grants instead of trusting a supplied/stale context.
        trusted = build_tenant_context(session, context.user_id, organisation_id)
        require_active_tenant_permission(session, trusted, permission, organisation_id)
        return trusted

    @staticmethod
    def source(
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
        from app.speaking_audio.evidence import AudioEvidenceError, resolve_submitted_audio

        try:
            return resolve_submitted_audio(
                session,
                asset_id,
                response_revision_id=response_revision_id,
                audio_sha256=audio_sha256,
                lock=lock,
            )
        except AudioEvidenceError as exc:
            raise ExecutionError(exc.category) from exc

    @classmethod
    def _verify_job_source(
        cls, session: Session, job: SpeechTranscriptionJob
    ) -> CandidateMediaAsset:
        from app.speech_transcription.profiles import snapshot_approval_valid

        try:
            asset, _, _, identity = cls.source(
                session,
                job.candidate_media_asset_id,
                response_revision_id=job.candidate_response_revision_id,
                audio_sha256=job.audio_sha256,
            )
        except (ValueError, PermissionError) as exc:
            raise ExecutionError("STALE_AUDIO_EVIDENCE") from exc
        if (
            identity != job.source_identity
            or _checksum(job.profile_snapshot) != job.snapshot_checksum
            or job.profile_snapshot.get("source") != identity
            or job.profile_snapshot.get("profile_version") != job.profile_version
        ):
            raise ExecutionError("STALE_AUDIO_EVIDENCE")
        if not snapshot_approval_valid(session, job.profile_snapshot):
            raise ExecutionError("PROFILE_APPROVAL_INVALID")
        return asset

    @staticmethod
    def _transition(
        job: SpeechTranscriptionJob,
        status: str,
        *,
        failure: str | None = None,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or _now()
        job.status, job.failure_category, job.updated_at = status, failure, timestamp
        entry: dict[str, Any] = {
            "status": status,
            "at": timestamp.isoformat(),
            "attempt_count": job.attempt_count,
        }
        if failure:
            entry["failure_category"] = failure
        job.state_history = [*(job.state_history or []), entry]
        if status in {"READY", "FAILED", "CANCELLED"}:
            job.completed_at = timestamp
            job.claim_token = None
            job.lease_expires_at = None

    @staticmethod
    def _audit(session: Session, job: SpeechTranscriptionJob, event: str) -> None:
        AuditService.record(
            session,
            TenantContext(
                user_id=job.requested_by_user_id,
                organisation_id=job.organisation_id,
                membership_id=job.requested_membership_id,
            ),
            event_type=event,
            entity_type="SPEECH_TRANSCRIPTION_JOB",
            entity_id=str(job.id),
            organisation_id=job.organisation_id,
            actor_type=ActorType.SYSTEM_SERVICE,
            require_audit_permission=False,
            metadata={
                "job_id": str(job.id),
                "speech_transcription_id": str(job.transcription_id),
                "candidate_media_asset_id": str(job.candidate_media_asset_id),
                "status": job.status,
                "attempt_count": job.attempt_count,
                "failure_category": job.failure_category,
                "profile_version": job.profile_version,
            },
        )

    @classmethod
    def request(
        cls,
        session: Session,
        *,
        context: TenantContext,
        asset_id: uuid.UUID,
        profile_id: uuid.UUID,
        language: str,
        operation_id: uuid.UUID,
        settings: Settings,
        response_revision_id: uuid.UUID | None = None,
        audio_sha256: str | None = None,
    ) -> SpeechTranscriptionJob:
        pilot.require_unrestricted(settings)
        from app.speech_transcription.profiles import approved_profile_record
        from app.speech_transcription.runtime import validate_profile_configuration

        asset, revision, binding, source = cls.source(
            session,
            asset_id,
            response_revision_id=response_revision_id,
            audio_sha256=audio_sha256,
            lock=True,
        )
        trusted = cls.authorize(
            session,
            context,
            asset.organisation_id,
            "speech_transcription.request",
            candidate_user_id=asset.candidate_user_id,
        )
        existing = session.scalar(
            select(SpeechTranscriptionJob).where(
                SpeechTranscriptionJob.operation_id == operation_id
            )
        )
        if existing is not None:
            if (
                existing.candidate_media_asset_id != asset.id
                or existing.candidate_response_revision_id != revision.id
                or existing.profile_id != profile_id
                or existing.language != language
                or existing.requested_by_user_id != trusted.user_id
            ):
                raise ExecutionError("TRANSCRIPTION_OPERATION_CONFLICT")
            return existing
        profile = SpeechTranscriptionService._profile(session, profile_id)
        if profile.approved_at is None or profile.approved_by is None:
            raise ExecutionError("TRANSCRIPTION_PROFILE_NOT_APPROVED")
        snapshot = snapshot_profile(profile, language, source)
        approval = approved_profile_record(session, profile, snapshot)
        if approval is None:
            raise ExecutionError("PROFILE_APPROVAL_INVALID")
        snapshot["approval"] = {
            "id": str(approval.id),
            "profile_version": snapshot["profile_version"],
            "approved_by": str(profile.approved_by),
        }
        availability = validate_profile_configuration(snapshot, settings)
        if availability.get("availability") not in {"CONFIGURED", "TEST_ONLY"}:
            raise ExecutionError(str(availability.get("unavailable_reason") or "UNCONFIGURED"))
        transcription = session.scalar(
            select(SpeechTranscription)
            .where(SpeechTranscription.asset_id == asset.id)
            .with_for_update()
        )
        if transcription is None:
            transcription = SpeechTranscription(
                asset_id=asset.id,
                operation_id=operation_id,
                language=language,
                status=TranscriptionStatus.CREATED,
            )
            session.add(transcription)
            session.flush()
        active = session.scalar(
            select(SpeechTranscriptionJob).where(
                SpeechTranscriptionJob.transcription_id == transcription.id,
                SpeechTranscriptionJob.status.in_(ACTIVE_STATES),
            )
        )
        if active is not None:
            raise ExecutionError("TRANSCRIPTION_ALREADY_ACTIVE")
        timestamp = _now()
        assert trusted.membership_id is not None
        job = SpeechTranscriptionJob(
            id=uuid.uuid4(),
            transcription_id=transcription.id,
            operation_id=operation_id,
            organisation_id=asset.organisation_id,
            candidate_user_id=asset.candidate_user_id,
            attempt_id=asset.attempt_id,
            exam_version_item_binding_id=asset.exam_version_item_binding_id,
            candidate_response_revision_id=revision.id,
            candidate_response_media_binding_id=binding.id,
            candidate_media_asset_id=asset.id,
            recording_session_id=asset.recording_session_id,
            requested_by_user_id=trusted.user_id,
            requested_membership_id=trusted.membership_id,
            audio_sha256=asset.sha256,
            byte_size=asset.byte_size,
            verified_duration_ms=source["duration_ms"],
            source_identity=source,
            profile_id=profile.id,
            profile_version=snapshot["profile_version"],
            profile_snapshot=snapshot,
            configuration_checksum=_checksum(snapshot["configuration"]),
            snapshot_checksum=_checksum(snapshot),
            language=language,
            status="QUEUED",
            attempt_count=0,
            max_attempts=3,
            next_attempt_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
            state_history=[],
        )
        cls._transition(job, "QUEUED", now=timestamp)
        session.add(job)
        transcription.status, transcription.failure_category = TranscriptionStatus.CREATED, None
        session.flush()
        cls._audit(session, job, "speech_transcription.requested")
        return job

    @classmethod
    def claim_next(
        cls,
        session: Session,
        *,
        worker_id: str,
        lease_seconds: int = 90,
        now: datetime | None = None,
    ) -> SpeechTranscriptionJob | None:
        if pilot.active():
            return None
        timestamp = now or _now()
        if not worker_id or len(worker_id) > 120 or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid worker claim configuration")
        eligible = or_(
            and_(
                SpeechTranscriptionJob.status.in_(("QUEUED", "RETRYABLE")),
                SpeechTranscriptionJob.next_attempt_at <= timestamp,
            ),
            and_(
                SpeechTranscriptionJob.status == "RUNNING",
                SpeechTranscriptionJob.lease_expires_at <= timestamp,
            ),
        )
        while True:
            job = session.scalar(
                select(SpeechTranscriptionJob)
                .where(eligible)
                .order_by(SpeechTranscriptionJob.created_at, SpeechTranscriptionJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
                .execution_options(populate_existing=True)
            )
            if job is None:
                return None
            if job.attempt_count >= job.max_attempts:
                cls._transition(job, "FAILED", failure="RETRY_EXHAUSTED", now=timestamp)
                root = session.get(SpeechTranscription, job.transcription_id)
                assert root is not None
                root.status, root.failure_category = TranscriptionStatus.FAILED, "RETRY_EXHAUSTED"
                cls._audit(session, job, "speech_transcription.failed")
                session.flush()
                continue
            job.attempt_count += 1
            job.claim_token, job.claimed_by = uuid.uuid4(), worker_id
            job.lease_expires_at = timestamp + timedelta(seconds=lease_seconds)
            job.started_at = job.started_at or timestamp
            cls._transition(job, "RUNNING", now=timestamp)
            root = session.get(SpeechTranscription, job.transcription_id)
            assert root is not None
            root.status, root.failure_category = TranscriptionStatus.RUNNING, None
            cls._audit(session, job, "speech_transcription.started")
            session.flush()
            return job

    @staticmethod
    def _fenced(
        session: Session, job_id: uuid.UUID, claim_token: uuid.UUID
    ) -> SpeechTranscriptionJob | None:
        job = session.scalar(
            select(SpeechTranscriptionJob)
            .where(SpeechTranscriptionJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            job is None
            or job.status != "RUNNING"
            or job.claim_token != claim_token
            or job.lease_expires_at is None
            or _utc(job.lease_expires_at) <= _now()
        ):
            return None
        return job

    @classmethod
    def heartbeat(
        cls, session: Session, *, job_id: uuid.UUID, claim_token: uuid.UUID, lease_seconds: int = 90
    ) -> bool:
        job = cls._fenced(session, job_id, claim_token)
        if job is None:
            return False
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid lease duration")
        job.lease_expires_at, job.updated_at = _now() + timedelta(seconds=lease_seconds), _now()
        session.flush()
        return True

    @staticmethod
    def _validate_result(
        result: TranscriptionResult, job: SpeechTranscriptionJob
    ) -> tuple[str, list[dict[str, Any]]]:
        snapshot = job.profile_snapshot
        if (
            not isinstance(result, TranscriptionResult)
            or not isinstance(result.transcript, str)
            or not isinstance(result.segments, tuple)
        ):
            raise InvalidProviderResult("invalid transcript structure")
        if (
            result.provider != snapshot["provider"]
            or result.model_name != snapshot["model_name"]
            or result.adapter_version != snapshot["adapter_version"]
        ):
            raise InvalidProviderResult("provider identity mismatch")
        if (
            not isinstance(result.metadata, dict)
            or result.metadata.get("language") != job.language
            or result.metadata.get("audio_sha256") != job.audio_sha256
            or result.metadata.get("execution_id") != str(job.id)
            or result.metadata.get("test_only")
            is not (snapshot["provider"] == "DETERMINISTIC_TEST")
        ):
            raise InvalidProviderResult("execution evidence identity mismatch")

        def valid_text(value: str, limit: int) -> str:
            if len(value) > limit or any(
                unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char not in "\n\r\t"
                for char in value
            ):
                raise InvalidProviderResult("invalid transcript text")
            normalized = SpeechTranscriptionService._normalise(value)
            if len(normalized) > limit:
                raise InvalidProviderResult("normalized transcript exceeds technical limits")
            return normalized

        transcript = valid_text(result.transcript, MAX_TRANSCRIPT_TEXT)
        if not transcript:
            raise EmptyProviderResult("provider returned no speech")
        if len(result.segments) > MAX_SEGMENTS:
            raise InvalidProviderResult("too many segments")
        segments: list[dict[str, Any]] = []
        seen: set[str] = set()
        previous_end = 0
        for segment in result.segments:
            if (
                not isinstance(segment.segment_id, str)
                or not segment.segment_id
                or len(segment.segment_id) > 120
                or segment.segment_id in seen
                or type(segment.start_ms) is not int
                or type(segment.end_ms) is not int
                or segment.start_ms < previous_end
                or segment.end_ms <= segment.start_ms
                or segment.end_ms > job.verified_duration_ms
                or not isinstance(segment.text, str)
            ):
                raise InvalidProviderResult("invalid segment identity or interval")
            value = valid_text(segment.text, MAX_SEGMENT_TEXT)
            if not value:
                raise InvalidProviderResult("empty segment text")
            item: dict[str, Any] = {
                "segment_id": segment.segment_id,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "text": value,
            }
            if segment.confidence is not None:
                if (
                    isinstance(segment.confidence, bool)
                    or not isinstance(segment.confidence, (int, float))
                    or not math.isfinite(segment.confidence)
                    or not 0 <= segment.confidence <= 1
                    or not snapshot["configuration"].get("confidence_semantics")
                ):
                    raise InvalidProviderResult("undefined or invalid confidence")
                item["confidence"] = segment.confidence
            segments.append(item)
            seen.add(segment.segment_id)
            previous_end = segment.end_ms
        return transcript, segments

    @classmethod
    def fail_claim(
        cls, session: Session, *, job_id: uuid.UUID, claim_token: uuid.UUID, category: str
    ) -> SpeechTranscriptionJob | None:
        job = cls._fenced(session, job_id, claim_token)
        if job is None:
            return None
        retryable = category in RETRYABLE_FAILURES and job.attempt_count < job.max_attempts
        cls._transition(job, "RETRYABLE" if retryable else "FAILED", failure=category)
        job.claim_token, job.lease_expires_at = None, None
        job.next_attempt_at = _now() + timedelta(seconds=min(30, 2**job.attempt_count))
        root = session.get(SpeechTranscription, job.transcription_id)
        assert root is not None
        root.status, root.failure_category, root.retry_count = (
            TranscriptionStatus.FAILED,
            category,
            max(job.attempt_count - 1, 0),
        )
        cls._audit(session, job, "speech_transcription.failed")
        session.flush()
        return job

    @classmethod
    def _execute_claim(
        cls,
        session_factory: SessionFactory,
        *,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        storage: ObjectStorage,
        settings: Settings,
        provider_factory: ProviderFactory | None = None,
    ) -> SpeechTranscriptionJob | None:
        from app.speech_transcription.runtime import (
            build_transcription_provider,
            validate_profile_configuration,
        )

        factory = provider_factory or build_transcription_provider
        try:
            with session_factory() as session:
                job = cls._fenced(session, job_id, claim_token)
                if job is None:
                    return None
                asset = cls._verify_job_source(session, job)
                cls.authorize(
                    session,
                    build_tenant_context(session, job.requested_by_user_id, job.organisation_id),
                    job.organisation_id,
                    "speech_transcription.request",
                    candidate_user_id=job.candidate_user_id,
                )
                if validate_profile_configuration(job.profile_snapshot, settings).get(
                    "availability"
                ) not in {"CONFIGURED", "TEST_ONLY"}:
                    raise ExecutionError("UNCONFIGURED")
                snapshot = copy.deepcopy(job.profile_snapshot)
                storage_key, expected_size, expected_sha = (
                    asset.storage_key,
                    job.byte_size,
                    job.audio_sha256,
                )
                language, duration = job.language, job.verified_duration_ms
                cls._audit(session, job, "speech_transcription.provider_requested")
                session.commit()  # No row lock or DB transaction during object/provider I/O.
            try:
                backend = SpeakingAudioService.durable_storage(storage)
                audio = backend.get_bounded(
                    storage_key, max_bytes=25_000_000, expected_size=expected_size
                )
            except StorageIntegrityError as exc:
                record(Signal.STORAGE_INTEGRITY, Failure.INTEGRITY_MISMATCH)
                raise ExecutionError("STALE_AUDIO_EVIDENCE") from exc
            except StorageError as exc:
                raise ExecutionError("OBJECT_STORAGE_UNAVAILABLE") from exc
            if len(audio) != expected_size or hashlib.sha256(audio).hexdigest() != expected_sha:
                record(Signal.STORAGE_INTEGRITY, Failure.INTEGRITY_MISMATCH)
                raise ExecutionError("STALE_AUDIO_EVIDENCE")
            provider = factory(snapshot, settings)
            if (provider.provider, provider.model_name, provider.adapter_version) != (
                snapshot["provider"],
                snapshot["model_name"],
                snapshot["adapter_version"],
            ):
                raise InvalidProviderResult("provider does not match pinned profile")
            result = provider.transcribe(
                audio,
                language=language,
                configuration={
                    **snapshot["configuration"],
                    "execution_id": str(job_id),
                    "audio_sha256": expected_sha,
                    "source_content_type": snapshot["source"]["content_type"],
                    "verified_duration_ms": duration,
                },
                limits={
                    "max_segments": MAX_SEGMENTS,
                    "max_segment_text": MAX_SEGMENT_TEXT,
                    "max_transcript_text": MAX_TRANSCRIPT_TEXT,
                },
            )
            with session_factory() as session:
                job = cls._fenced(session, job_id, claim_token)
                if job is None:
                    return None
                cls._verify_job_source(session, job)
                cls.authorize(
                    session,
                    build_tenant_context(session, job.requested_by_user_id, job.organisation_id),
                    job.organisation_id,
                    "speech_transcription.request",
                    candidate_user_id=job.candidate_user_id,
                )
                transcript, segments = cls._validate_result(result, job)
                root = session.scalar(
                    select(SpeechTranscription)
                    .where(SpeechTranscription.id == job.transcription_id)
                    .with_for_update()
                )
                assert root is not None
                previous = SpeechTranscriptionService.current_version(session, root.id)
                version_number = (
                    session.scalar(
                        select(SpeechTranscriptionVersion.version_number)
                        .where(SpeechTranscriptionVersion.transcription_id == root.id)
                        .order_by(SpeechTranscriptionVersion.version_number.desc())
                        .limit(1)
                    )
                    or 0
                ) + 1
                version = SpeechTranscriptionVersion(
                    transcription_id=root.id,
                    version_number=version_number,
                    candidate_media_asset_id=job.candidate_media_asset_id,
                    audio_sha256=job.audio_sha256,
                    profile_id=job.profile_id,
                    profile_version=job.profile_version,
                    execution_job_id=job.id,
                    transcript=transcript,
                    segments=segments,
                    checksum=_checksum(
                        {
                            "execution_id": str(job.id),
                            "snapshot_checksum": job.snapshot_checksum,
                            "transcript": transcript,
                            "segments": segments,
                        }
                    ),
                    status=TranscriptionStatus.READY,
                    provider=snapshot["provider"],
                    model_name=snapshot["model_name"],
                    adapter_version=snapshot["adapter_version"],
                    language=job.language,
                    configuration=copy.deepcopy(snapshot["configuration"]),
                    configuration_checksum=job.configuration_checksum,
                    retry_count=max(job.attempt_count - 1, 0),
                    created_at=_now(),
                    supersedes_version_id=previous.id if previous else None,
                    provenance={
                        "source": copy.deepcopy(job.source_identity),
                        "execution_id": str(job.id),
                        "profile_version": job.profile_version,
                        "snapshot_checksum": job.snapshot_checksum,
                        "test_only": snapshot["provider"] == "DETERMINISTIC_TEST",
                        "evidence_type": "DERIVED_TRANSCRIPT",
                        "attempt_count": job.attempt_count,
                    },
                )
                session.add(version)
                if previous is not None:
                    previous.status = TranscriptionStatus.SUPERSEDED
                cls._transition(job, "READY")
                root.status, root.failure_category, root.language, root.retry_count = (
                    TranscriptionStatus.READY,
                    None,
                    job.language,
                    max(job.attempt_count - 1, 0),
                )
                cls._audit(session, job, "speech_transcription.completed")
                session.commit()
                return job
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, ProviderError):
                category = exc.category
            elif isinstance(exc, ExecutionError):
                category = exc.category
            elif isinstance(exc, AuthorizationError):
                category = "REQUEST_AUTHORITY_REVOKED"
            else:
                from sqlalchemy.exc import SQLAlchemyError

                category = (
                    "DATABASE_UNAVAILABLE"
                    if isinstance(exc, SQLAlchemyError)
                    else "INVALID_PROVIDER_OUTPUT"
                )
            with session_factory() as session:
                job = cls.fail_claim(
                    session, job_id=job_id, claim_token=claim_token, category=category
                )
                session.commit()
                return job

    @classmethod
    def execute_claim(
        cls,
        session_factory: SessionFactory,
        *,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        storage: ObjectStorage,
        settings: Settings,
        provider_factory: ProviderFactory | None = None,
    ) -> SpeechTranscriptionJob | None:
        pilot.require_unrestricted(settings)
        """Renew a claimed lease independently while private I/O is in flight."""
        with session_factory() as session:
            job = session.get(SpeechTranscriptionJob, job_id)
            if job is None or job.lease_expires_at is None:
                return None
            lease_seconds = min(
                3600, max(1, math.ceil((_utc(job.lease_expires_at) - _now()).total_seconds()))
            )
        stop = threading.Event()

        def renew() -> None:
            while not stop.wait(max(0.2, lease_seconds / 3)):
                try:
                    with session_factory() as session:
                        renewed = cls.heartbeat(
                            session,
                            job_id=job_id,
                            claim_token=claim_token,
                            lease_seconds=lease_seconds,
                        )
                        session.commit()
                    if not renewed:
                        return
                except Exception:  # noqa: BLE001
                    # An outage never extends ownership implicitly. Completion
                    # still needs the live fencing token and unexpired lease.
                    continue

        heartbeat = threading.Thread(target=renew, daemon=True, name="transcription-lease")
        heartbeat.start()
        try:
            return cls._execute_claim(
                session_factory,
                job_id=job_id,
                claim_token=claim_token,
                storage=storage,
                settings=settings,
                provider_factory=provider_factory,
            )
        finally:
            stop.set()
            heartbeat.join(timeout=1)

    @classmethod
    def retry(
        cls, session: Session, *, job: SpeechTranscriptionJob, context: TenantContext
    ) -> SpeechTranscriptionJob:
        cls.authorize(
            session,
            context,
            job.organisation_id,
            "speech_transcription.request",
            candidate_user_id=job.candidate_user_id,
        )
        if not cls.can_retry(job):
            raise ExecutionError("TRANSCRIPTION_RETRY_NOT_ALLOWED")
        cls._verify_job_source(session, job)
        job.next_attempt_at = _now()
        cls._transition(job, "RETRYABLE", failure=job.failure_category)
        cls._audit(session, job, "speech_transcription.retry_requested")
        return job

    @classmethod
    def cancel(
        cls, session: Session, *, job: SpeechTranscriptionJob, context: TenantContext
    ) -> SpeechTranscriptionJob:
        cls.authorize(
            session,
            context,
            job.organisation_id,
            "speech_transcription.request",
            candidate_user_id=job.candidate_user_id,
        )
        if job.status not in ACTIVE_STATES:
            raise ExecutionError("TRANSCRIPTION_CANCEL_NOT_ALLOWED")
        cls._transition(job, "CANCELLED", failure="CANCELLED_BY_REVIEWER")
        root = session.get(SpeechTranscription, job.transcription_id)
        assert root is not None
        root.status, root.failure_category = TranscriptionStatus.FAILED, "CANCELLED_BY_REVIEWER"
        cls._audit(session, job, "speech_transcription.cancelled")
        return job

    @staticmethod
    def can_retry(job: SpeechTranscriptionJob) -> bool:
        return (
            job.status == "RETRYABLE"
            and job.attempt_count < job.max_attempts
            and job.failure_category in RETRYABLE_FAILURES
        )

    @staticmethod
    def payload(session: Session, job: SpeechTranscriptionJob) -> dict[str, Any]:
        version_id = session.scalar(
            select(SpeechTranscriptionVersion.id).where(
                SpeechTranscriptionVersion.execution_job_id == job.id
            )
        )
        snapshot = job.profile_snapshot
        return {
            "id": str(job.id),
            "transcription_id": str(job.transcription_id),
            "asset_id": str(job.candidate_media_asset_id),
            "candidate_response_revision_id": str(job.candidate_response_revision_id),
            "audio_sha256": job.audio_sha256,
            "verified_duration_ms": job.verified_duration_ms,
            "status": job.status,
            "profile_id": str(job.profile_id),
            "profile_version": job.profile_version,
            "provider": snapshot["provider"],
            "model_name": snapshot["model_name"],
            "adapter_version": snapshot["adapter_version"],
            "language": job.language,
            "test_only": snapshot["provider"] == "DETERMINISTIC_TEST",
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            "created_at": job.created_at.isoformat(),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "failure_category": job.failure_category,
            "transcription_version_id": str(version_id) if version_id else None,
            "can_retry": TranscriptionExecutionService.can_retry(job),
            "can_cancel": job.status in ACTIVE_STATES,
            "state_history": job.state_history,
        }
