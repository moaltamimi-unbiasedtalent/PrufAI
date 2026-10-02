from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import ActorType
from app.models.identity import enum_type


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_organisation_timestamp", "organisation_id", "timestamp"),
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(enum_type(ActorType), nullable=False)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid(as_uuid=True))
    organisation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("organisations.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(120))
    entity_version: Mapped[Optional[str]] = mapped_column(String(80))
    request_id: Mapped[Optional[str]] = mapped_column(String(120))
    workflow_id: Mapped[Optional[str]] = mapped_column(String(120))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON)
    previous_state_hash: Mapped[Optional[str]] = mapped_column(String(128))
    new_state_hash: Mapped[Optional[str]] = mapped_column(String(128))
