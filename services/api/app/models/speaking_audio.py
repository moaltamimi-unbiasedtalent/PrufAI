from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import CandidateMediaStatus, TranscriptionStatus
from app.models.identity import enum_type


class CandidateAudioRecordingSession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_audio_recording_sessions"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "exam_version_item_binding_id",
            "attempt_number",
            name="uq_audio_recording_attempt",
        ),
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id", ondelete="CASCADE"), nullable=False
    )
    exam_version_item_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_version_item_bindings.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    recording_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    recording_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[CandidateMediaStatus] = mapped_column(
        enum_type(CandidateMediaStatus), nullable=False
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, unique=True)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    asset: Mapped[Optional[CandidateMediaAsset]] = relationship(back_populates="recording_session")


class CandidateMediaAsset(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_media_assets"
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id", ondelete="CASCADE"), nullable=False
    )
    exam_version_item_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_version_item_bindings.id", ondelete="RESTRICT"), nullable=False
    )
    recording_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_audio_recording_sessions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[CandidateMediaStatus] = mapped_column(
        enum_type(CandidateMediaStatus), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recording_session: Mapped[CandidateAudioRecordingSession] = relationship(back_populates="asset")
    transcriptions: Mapped[list[SpeechTranscription]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )


class CandidateResponseMediaBinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_response_media_bindings"
    __table_args__ = (
        UniqueConstraint(
            "candidate_response_revision_id",
            "binding_role",
            name="uq_response_media_binding_role",
        ),
    )
    candidate_response_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_response_revisions.id", ondelete="CASCADE"), nullable=False
    )
    candidate_media_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_media_assets.id", ondelete="RESTRICT"), nullable=False
    )
    binding_role: Mapped[str] = mapped_column(String(40), nullable=False, default="PRIMARY_AUDIO")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SpeechTranscription(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "speech_transcriptions"
    __table_args__ = (UniqueConstraint("asset_id", name="uq_speech_transcription_asset"),)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_media_assets.id", ondelete="CASCADE"), nullable=False
    )
    operation_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True, unique=True)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[TranscriptionStatus] = mapped_column(
        enum_type(TranscriptionStatus), nullable=False
    )
    failure_category: Mapped[Optional[str]] = mapped_column(String(80))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    versions: Mapped[list[SpeechTranscriptionVersion]] = relationship(
        back_populates="transcription", cascade="all, delete-orphan"
    )
    asset: Mapped[CandidateMediaAsset] = relationship(back_populates="transcriptions")


class SpeechTranscriptionVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "speech_transcription_versions"
    __table_args__ = (
        UniqueConstraint(
            "transcription_id", "version_number", name="uq_speech_transcription_version"
        ),
    )
    transcription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("speech_transcriptions.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_media_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_media_assets.id", ondelete="RESTRICT"), nullable=False
    )
    audio_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_model_profiles.id"))
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[TranscriptionStatus] = mapped_column(
        enum_type(TranscriptionStatus), nullable=False, default=TranscriptionStatus.READY
    )
    provider: Mapped[Optional[str]] = mapped_column(String(120))
    model_name: Mapped[Optional[str]] = mapped_column(String(160))
    adapter_version: Mapped[Optional[str]] = mapped_column(String(80))
    language: Mapped[Optional[str]] = mapped_column(String(20))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    configuration_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    failure_category: Mapped[Optional[str]] = mapped_column(String(80))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supersedes_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("speech_transcription_versions.id", ondelete="SET NULL")
    )
    transcription: Mapped[SpeechTranscription] = relationship(back_populates="versions")


@event.listens_for(SpeechTranscriptionVersion, "before_update")
def _prevent_completed_transcription_mutation(
    _mapper: Mapper[SpeechTranscriptionVersion],
    _connection: Any,
    target: SpeechTranscriptionVersion,
) -> None:
    """Completed versions are immutable; corrections are new versions."""
    from sqlalchemy import inspect

    state = inspect(target)
    status_history = state.attrs["status"].history
    previous_status = status_history.deleted[0] if status_history.deleted else target.status
    if target.status not in (
        TranscriptionStatus.READY,
        TranscriptionStatus.SUPERSEDED,
    ) and previous_status not in (
        TranscriptionStatus.READY,
        TranscriptionStatus.SUPERSEDED,
    ):
        return
    protected = {
        "transcription_id",
        "version_number",
        "candidate_media_asset_id",
        "audio_sha256",
        "profile_id",
        "transcript",
        "segments",
        "checksum",
        "provider",
        "model_name",
        "adapter_version",
        "configuration",
        "configuration_checksum",
        "language",
    }
    if any(state.attrs[name].history.has_changes() for name in protected if name in state.attrs):
        raise ValueError("completed transcription versions are immutable")
