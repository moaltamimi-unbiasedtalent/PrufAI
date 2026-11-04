"""Durable execution state for the existing transcription evidence aggregate."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    event,
    select,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class SpeechTranscriptionJob(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "speech_transcription_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','RETRYABLE','READY','FAILED','CANCELLED')",
            name="ck_transcription_job_state",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 3",
            name="ck_transcription_job_attempts",
        ),
        Index("ix_transcription_job_claim", "status", "next_attempt_at", "lease_expires_at"),
        Index(
            "uq_transcription_job_active",
            "transcription_id",
            unique=True,
            postgresql_where=text("status IN ('QUEUED','RUNNING','RETRYABLE')"),
            sqlite_where=text("status IN ('QUEUED','RUNNING','RETRYABLE')"),
        ),
    )
    transcription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("speech_transcriptions.id", ondelete="RESTRICT"), nullable=False
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, unique=True)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id"), nullable=False
    )
    exam_version_item_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_version_item_bindings.id"), nullable=False
    )
    candidate_response_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_response_revisions.id"), nullable=False
    )
    candidate_response_media_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_response_media_bindings.id"), nullable=False
    )
    candidate_media_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_media_assets.id"), nullable=False
    )
    recording_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_audio_recording_sessions.id"), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    requested_membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisation_memberships.id"), nullable=False
    )
    audio_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    verified_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source_identity: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_model_profiles.id"), nullable=False
    )
    profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    configuration_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[Optional[uuid.UUID]] = mapped_column()
    claimed_by: Mapped[Optional[str]] = mapped_column(String(120))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failure_category: Mapped[Optional[str]] = mapped_column(String(80))
    state_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


JOB_PINS = {
    "transcription_id",
    "operation_id",
    "organisation_id",
    "candidate_user_id",
    "attempt_id",
    "exam_version_item_binding_id",
    "candidate_response_revision_id",
    "candidate_response_media_binding_id",
    "candidate_media_asset_id",
    "recording_session_id",
    "requested_by_user_id",
    "requested_membership_id",
    "audio_sha256",
    "byte_size",
    "verified_duration_ms",
    "source_identity",
    "profile_id",
    "profile_version",
    "profile_snapshot",
    "configuration_checksum",
    "snapshot_checksum",
    "language",
    "max_attempts",
    "created_at",
}


def _protect_execution_history(session: Session) -> None:
    """Compare persisted JSON too: plain JSON columns do not track nested edits."""
    from app.models.speaking_audio import SpeechTranscriptionVersion

    if any(
        isinstance(item, (SpeechTranscriptionJob, SpeechTranscriptionVersion))
        for item in session.deleted
    ):
        raise ValueError("transcription execution and evidence history cannot be deleted")
    for item in list(session.identity_map.values()):
        if not isinstance(item, (SpeechTranscriptionJob, SpeechTranscriptionVersion)):
            continue
        stored = (
            session.connection()
            .execute(select(item.__table__).where(item.__table__.c.id == item.id))
            .mappings()
            .first()
        )
        if stored is None:
            continue
        if isinstance(item, SpeechTranscriptionJob):
            protected = JOB_PINS
            history = stored["state_history"]
            if item.state_history[: len(history)] != history:
                raise ValueError("transcription execution state history is append-only")
        else:
            if stored["status"] not in {"READY", "SUPERSEDED"}:
                continue
            if item.status not in (
                {"READY", "SUPERSEDED"} if stored["status"] == "READY" else {"SUPERSEDED"}
            ):
                raise ValueError("completed transcription version status cannot be rewritten")
            protected = set(stored.keys()) - {"status"}
        for name in protected:
            left, right = stored[name], getattr(item, name)
            if isinstance(left, datetime) and isinstance(right, datetime):
                # SQLite omits the timezone on retrieval.
                left = (
                    left.replace(tzinfo=timezone.utc)
                    if left.tzinfo is None
                    else left.astimezone(timezone.utc)
                )
                right = (
                    right.replace(tzinfo=timezone.utc)
                    if right.tzinfo is None
                    else right.astimezone(timezone.utc)
                )
            if left != right:
                raise ValueError(
                    "transcription execution identity and completed versions are immutable"
                )


@event.listens_for(Session, "before_flush")
def _before_execution_flush(session: Session, _context: Any, _instances: Any) -> None:
    _protect_execution_history(session)


@event.listens_for(Session, "before_commit")
def _before_execution_commit(session: Session) -> None:
    _protect_execution_history(session)
