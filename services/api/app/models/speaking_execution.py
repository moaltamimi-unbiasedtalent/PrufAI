"""Durable Speaking execution receipt attached to existing AI and assessment roots."""

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


class SpeakingExecutionJob(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "speaking_execution_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','RETRYABLE','COMPLETED','FAILED','CANCELLED')",
            name="ck_speaking_execution_state",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts AND max_attempts BETWEEN 1 AND 3",
            name="ck_speaking_execution_attempts",
        ),
        Index("ix_speaking_execution_claim", "status", "next_attempt_at", "lease_expires_at"),
        Index(
            "uq_speaking_execution_active",
            "attempt_id",
            "exam_version_item_binding_id",
            unique=True,
            postgresql_where=text("status IN ('QUEUED','RUNNING','RETRYABLE')"),
            sqlite_where=text("status IN ('QUEUED','RUNNING','RETRYABLE')"),
        ),
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, unique=True)
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id", ondelete="RESTRICT"), nullable=False
    )
    exam_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), nullable=False
    )
    exam_version_item_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_version_item_bindings.id", ondelete="RESTRICT"), nullable=False
    )
    question_bank_item_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("question_bank_item_versions.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_response_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_responses.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_response_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_response_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    standard_pack_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("standard_pack_versions.id", ondelete="RESTRICT"), nullable=False
    )
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubric_versions.id", ondelete="RESTRICT"), nullable=False
    )
    assessment_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subjective_assessment_versions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    ai_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    requested_membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisation_memberships.id", ondelete="RESTRICT"), nullable=False
    )
    execution_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_workflow_versions.id", ondelete="RESTRICT"), nullable=False
    )
    active_step_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("ai_run_steps.id", ondelete="RESTRICT")
    )
    candidate_media_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_media_assets.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_response_media_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_response_media_bindings.id", ondelete="RESTRICT"), nullable=False
    )
    recording_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_audio_recording_sessions.id", ondelete="RESTRICT"), nullable=False
    )
    audio_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    recording_policy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    audio_transport: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    provider_input_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    response_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    standard_pack_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    rubric_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    input_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    output_checksum: Mapped[Optional[str]] = mapped_column(String(64))
    provider_request_id: Mapped[Optional[str]] = mapped_column(String(160))
    source_identity: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    execution_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    validated_proposal: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[Optional[uuid.UUID]] = mapped_column()
    claimed_by: Mapped[Optional[str]] = mapped_column(String(120))
    failure_category: Mapped[Optional[str]] = mapped_column(String(80))
    state_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


PINS = {
    "candidate_media_asset_id",
    "candidate_response_media_binding_id",
    "recording_session_id",
    "audio_sha256",
    "verified_duration_ms",
    "recording_policy_checksum",
    "byte_size",
    "content_type",
    "operation_id",
    "created_at",
    "max_attempts",
    "source_identity",
    "execution_snapshot",
    "response_checksum",
    "standard_pack_checksum",
    "rubric_checksum",
    "profile_version",
    "snapshot_checksum",
    "input_checksum",
} | {
    "assessment_version_id",
    "candidate_response_id",
    "attempt_id",
    "requested_membership_id",
    "requested_by_user_id",
    "organisation_id",
    "candidate_user_id",
    "exam_version_item_binding_id",
    "question_bank_item_version_id",
    "standard_pack_version_id",
    "rubric_version_id",
    "ai_run_id",
    "candidate_response_revision_id",
    "exam_version_id",
    "execution_profile_id",
}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, datetime) and isinstance(b, datetime):
        a = a.replace(tzinfo=timezone.utc) if a.tzinfo is None else a.astimezone(timezone.utc)
        b = b.replace(tzinfo=timezone.utc) if b.tzinfo is None else b.astimezone(timezone.utc)
    return bool(a == b)


def _protect(session: Session) -> None:
    from app.models.ai_orchestration import AIRun, AIRunStep
    from app.models.writing_assessment import (
        SubjectiveAssessmentVersion,
        SubjectiveCriterionAssessment,
    )

    for row in list(session.deleted):
        if isinstance(row, SpeakingExecutionJob):
            raise ValueError("Speaking execution history cannot be deleted")
        if isinstance(row, (SubjectiveAssessmentVersion, SubjectiveCriterionAssessment)):
            version_id = (
                row.id
                if isinstance(row, SubjectiveAssessmentVersion)
                else row.subjective_assessment_version_id
            )
            if session.scalar(
                select(SpeakingExecutionJob.id).where(
                    SpeakingExecutionJob.assessment_version_id == version_id
                )
            ):
                raise ValueError("Speaking proposal history cannot be deleted")
        if isinstance(row, (AIRun, AIRunStep)):
            run_id = row.id if isinstance(row, AIRun) else row.run_id
            if session.scalar(
                select(SpeakingExecutionJob.id).where(SpeakingExecutionJob.ai_run_id == run_id)
            ):
                raise ValueError("Speaking execution run history cannot be deleted")
    for row in list(session.new):
        if isinstance(row, SubjectiveCriterionAssessment):
            version_id = row.subjective_assessment_version_id
            if version_id is None and row.assessment_version is not None:
                version_id = row.assessment_version.id
            version = session.get(SubjectiveAssessmentVersion, version_id) if version_id else None
            if (
                version
                and version.status.value
                in {"AI_PROPOSED", "SUPERSEDED", "REJECTED", "CHANGES_REQUESTED"}
                and session.scalar(
                    select(SpeakingExecutionJob.id).where(
                        SpeakingExecutionJob.assessment_version_id == version.id,
                        SpeakingExecutionJob.status == "COMPLETED",
                    )
                )
            ):
                stored_version = (
                    session.connection()
                    .execute(
                        select(SubjectiveAssessmentVersion.__table__.c.status).where(
                            SubjectiveAssessmentVersion.__table__.c.id == version.id
                        )
                    )
                    .scalar_one_or_none()
                )
                if stored_version is not None and stored_version.value in {
                    "AI_PROPOSED",
                    "SUPERSEDED",
                    "REJECTED",
                    "CHANGES_REQUESTED",
                }:
                    raise ValueError("executed Speaking proposal criteria cannot be extended")
    for row in list(session.identity_map.values()):
        if not isinstance(
            row,
            (
                SpeakingExecutionJob,
                AIRun,
                AIRunStep,
                SubjectiveAssessmentVersion,
                SubjectiveCriterionAssessment,
            ),
        ):
            continue
        stored = (
            session.connection()
            .execute(select(row.__table__).where(row.__table__.c.id == row.id))
            .mappings()
            .first()
        )
        if stored is None:
            continue
        if isinstance(row, (SubjectiveAssessmentVersion, SubjectiveCriterionAssessment)):
            version_id = (
                row.id
                if isinstance(row, SubjectiveAssessmentVersion)
                else row.subjective_assessment_version_id
            )
            linked = session.scalar(
                select(SpeakingExecutionJob).where(
                    SpeakingExecutionJob.assessment_version_id == version_id
                )
            )
            if linked is None or linked.status != "COMPLETED":
                continue
            if isinstance(row, SubjectiveAssessmentVersion):
                status = (
                    stored["status"].value
                    if hasattr(stored["status"], "value")
                    else stored["status"]
                )
                if status not in {
                    "AI_PROPOSED",
                    "APPROVED",
                    "SUPERSEDED",
                    "REJECTED",
                    "CHANGES_REQUESTED",
                }:
                    continue
                permitted = (
                    {"AI_PROPOSED", "SUPERSEDED", "REJECTED", "CHANGES_REQUESTED"}
                    if status == "AI_PROPOSED"
                    else {status}
                )
                if row.status.value not in permitted:
                    raise ValueError("executed Speaking proposal state cannot be rewritten")
                protected = set(stored.keys()) - {"status"}
            else:
                version = session.get(SubjectiveAssessmentVersion, version_id)
                if version is None or version.status.value not in {
                    "AI_PROPOSED",
                    "APPROVED",
                    "SUPERSEDED",
                    "REJECTED",
                    "CHANGES_REQUESTED",
                }:
                    continue
                protected = set(stored.keys())
        elif isinstance(row, SpeakingExecutionJob):
            if row.state_history[: len(stored["state_history"])] != stored["state_history"]:
                raise ValueError("Speaking execution state history is append-only")
            protected = (
                set(stored.keys())
                if stored["status"] in {"COMPLETED", "FAILED", "CANCELLED"}
                else set(PINS)
            )
            if stored["provider_input_checksum"]:
                protected |= {"provider_input_checksum", "audio_transport"}
        else:
            run_id = row.id if isinstance(row, AIRun) else row.run_id
            if not session.scalar(
                select(SpeakingExecutionJob.id).where(SpeakingExecutionJob.ai_run_id == run_id)
            ):
                continue
            status = (
                stored["status"].value if hasattr(stored["status"], "value") else stored["status"]
            )
            if status not in {"SUCCEEDED", "FAILED", "CANCELLED", "REJECTED"}:
                continue
            protected = set(stored.keys())
        if any(not _same(stored[key], getattr(row, key)) for key in protected):
            raise ValueError("Speaking execution identity and completed run evidence are immutable")


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, _context: Any, _instances: Any) -> None:
    _protect(session)


@event.listens_for(Session, "before_commit")
def _before_commit(session: Session) -> None:
    _protect(session)
