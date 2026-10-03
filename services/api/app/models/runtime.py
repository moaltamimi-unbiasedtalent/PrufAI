from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import CandidateAssignmentStatus, CandidateAttemptStatus
from app.models.identity import enum_type


class CandidateExamAssignment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_exam_assignments"
    __table_args__ = (
        CheckConstraint("maximum_attempts > 0", name="ck_candidate_assignment_attempts"),
        CheckConstraint(
            "available_until IS NULL OR available_from IS NULL OR available_until > available_from",
            name="ck_candidate_assignment_window",
        ),
        Index("ix_candidate_assignment_candidate_status", "candidate_user_id", "status"),
        Index("ix_candidate_assignment_org_status", "organisation_id", "status"),
    )

    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    exam_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[CandidateAssignmentStatus] = mapped_column(
        enum_type(CandidateAssignmentStatus),
        default=CandidateAssignmentStatus.ASSIGNED,
        nullable=False,
    )
    available_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    available_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    maximum_attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancelled_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    attempts: Mapped[list[CandidateExamAttempt]] = relationship(
        back_populates="assignment",
        cascade="all, delete-orphan",
        order_by="CandidateExamAttempt.attempt_number",
    )


class CandidateExamAttempt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_exam_attempts"
    __table_args__ = (
        CheckConstraint("attempt_number > 0", name="ck_candidate_attempt_number"),
        UniqueConstraint("assignment_id", "attempt_number", name="uq_candidate_attempt_number"),
        Index("ix_candidate_attempt_status_deadline", "status", "deadline_at"),
        Index(
            "uq_candidate_active_attempt",
            "assignment_id",
            unique=True,
            postgresql_where="status IN ('READY', 'IN_PROGRESS')",
        ),
    )

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_assignments.id", ondelete="CASCADE"), nullable=False
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    organisation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    exam_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_versions.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[CandidateAttemptStatus] = mapped_column(
        enum_type(CandidateAttemptStatus), default=CandidateAttemptStatus.CREATED, nullable=False
    )
    delivery_manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_plan_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_plan: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    assignment: Mapped[CandidateExamAssignment] = relationship(back_populates="attempts")
    session: Mapped[Optional[CandidateExamSession]] = relationship(
        back_populates="attempt", uselist=False, cascade="all, delete-orphan"
    )
    responses: Mapped[list[CandidateResponse]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )


class CandidateExamSession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_exam_sessions"
    __table_args__ = (Index("ix_candidate_session_last_seen", "last_seen_at"),)

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    current_module_id: Mapped[Optional[uuid.UUID]] = mapped_column()
    current_section_id: Mapped[Optional[uuid.UUID]] = mapped_column()
    current_task_id: Mapped[Optional[uuid.UUID]] = mapped_column()
    current_binding_id: Mapped[Optional[uuid.UUID]] = mapped_column()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    attempt: Mapped[CandidateExamAttempt] = relationship(back_populates="session")


class CandidateResponse(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_responses"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id", "exam_version_item_binding_id", name="uq_candidate_response_binding"
        ),
        Index("ix_candidate_response_attempt", "attempt_id"),
    )

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id", ondelete="CASCADE"), nullable=False
    )
    exam_version_item_binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exam_version_item_bindings.id", ondelete="RESTRICT"), nullable=False
    )
    current_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_schema_version: Mapped[str] = mapped_column(String(40), default="1", nullable=False)
    current_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    first_saved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_saved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    attempt: Mapped[CandidateExamAttempt] = relationship(back_populates="responses")
    revisions: Mapped[list[CandidateResponseRevision]] = relationship(
        back_populates="response",
        cascade="all, delete-orphan",
        order_by="CandidateResponseRevision.revision_number",
    )


class CandidateResponseRevision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "candidate_response_revisions"
    __table_args__ = (
        UniqueConstraint(
            "candidate_response_id", "revision_number", name="uq_candidate_response_revision"
        ),
        UniqueConstraint("attempt_id", "client_operation_id", name="uq_candidate_operation"),
        CheckConstraint("revision_number > 0", name="ck_candidate_revision_number"),
    )

    candidate_response_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_responses.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_exam_attempts.id", ondelete="CASCADE"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    client_operation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    client_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    response: Mapped[CandidateResponse] = relationship(back_populates="revisions")
