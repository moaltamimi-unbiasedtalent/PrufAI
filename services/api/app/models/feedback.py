"""Immutable feedback snapshots and their append-only governance receipts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class GovernedFeedback(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "governed_feedback"
    __table_args__ = (
        CheckConstraint(
            "source_kind IN ('EVALUATION_FAILURE','REVIEWER_FLAG',"
            "'GOVERNED_APPEAL_OUTCOME','OPERATIONAL_FAILURE')",
            name="ck_feedback_source_kind",
        ),
        Index("ix_feedback_scope", "organisation_id", "scope_key"),
        Index("ix_feedback_created", "organisation_id", "created_at"),
    )

    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT")
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(48), nullable=False)
    privacy_class: Mapped[str] = mapped_column(String(48), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeedbackPattern(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "feedback_pattern_versions"
    __table_args__ = (
        UniqueConstraint("group_key", "version_number", name="uq_feedback_pattern_version"),
        CheckConstraint("version_number > 0", name="ck_feedback_pattern_version"),
        Index("ix_feedback_pattern_created", "organisation_id", "created_at"),
    )

    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT")
    )
    group_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ImprovementProposal(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "improvement_proposal_versions"
    __table_args__ = (
        UniqueConstraint("proposal_id", "version_number", name="uq_improvement_proposal_version"),
        CheckConstraint("version_number > 0", name="ck_improvement_proposal_version"),
        Index("ix_improvement_proposal_created", "organisation_id", "created_at"),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT")
    )
    pattern_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feedback_pattern_versions.id", ondelete="RESTRICT"), nullable=False
    )
    body: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeedbackOperation(UUIDPrimaryKeyMixin, Base):
    """The caller's operation ID identifies a single immutable state receipt."""

    __tablename__ = "feedback_operations"
    __table_args__ = (
        UniqueConstraint(
            "entity_kind", "entity_id", "revision", name="uq_feedback_operation_state"
        ),
        CheckConstraint(
            "entity_kind IN ('FEEDBACK','PATTERN','PROPOSAL')",
            name="ck_feedback_operation_kind",
        ),
        CheckConstraint(
            "(revision = 1 AND previous_checksum IS NULL) OR "
            "(revision > 1 AND previous_checksum IS NOT NULL)",
            name="ck_feedback_operation_predecessor",
        ),
    )

    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT")
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    entity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    request_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_checksum: Mapped[str | None] = mapped_column(String(64))
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


_MODELS = (GovernedFeedback, FeedbackPattern, ImprovementProposal, FeedbackOperation)


def _protect_history(session: Session) -> None:
    for row in session.deleted:
        if isinstance(row, _MODELS):
            raise ValueError("FEEDBACK_HISTORY_IMMUTABLE")
    for row in session.identity_map.values():
        if not isinstance(row, _MODELS):
            continue
        if session.is_modified(row, include_collections=False):
            raise ValueError("FEEDBACK_HISTORY_IMMUTABLE")
        # SQLAlchemy's ordinary JSON type does not track in-place nested edits.
        table = row.__table__
        column = table.c[
            "state"
            if isinstance(row, FeedbackOperation)
            else "source"
            if isinstance(row, GovernedFeedback)
            else "body"
        ]
        stored = session.connection().scalar(select(column).where(table.c.id == row.id))
        if stored != getattr(row, column.name):
            raise ValueError("FEEDBACK_HISTORY_IMMUTABLE")


event.listen(Session, "before_flush", lambda session, *_: _protect_history(session))
event.listen(Session, "before_commit", _protect_history)
