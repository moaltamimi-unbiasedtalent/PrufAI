import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization import AuthorizationService, TenantContext
from app.models import AuditEvent
from app.models.enums import ActorType


class AuditRecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(min_length=3, max_length=160)
    entity_type: str = Field(min_length=1, max_length=120)
    entity_id: Optional[str] = Field(default=None, max_length=120)
    entity_version: Optional[str] = Field(default=None, max_length=80)
    request_id: Optional[str] = Field(default=None, max_length=120)
    workflow_id: Optional[str] = Field(default=None, max_length=120)
    reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    previous_state_hash: Optional[str] = Field(default=None, max_length=128)
    new_state_hash: Optional[str] = Field(default=None, max_length=128)

    @field_validator("event_type")
    @classmethod
    def valid_event_type(cls, value: str) -> str:
        if re.fullmatch(r"[a-z0-9_]+(?:\.[a-z0-9_]+)+", value) is None:
            raise ValueError("event_type must use dot-delimited lowercase names")
        return value


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("password", "secret", "token", "api_key")):
                return True
            if _contains_sensitive_key(child):
                return True
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(child) for child in value)
    return False


class AuditService:
    """The only application write path for audit events; no update/delete API exists."""

    @staticmethod
    def record(
        session: Session,
        context: TenantContext,
        *,
        event_type: str,
        entity_type: str,
        organisation_id: Optional[uuid.UUID] = None,
        actor_type: ActorType = ActorType.HUMAN,
        **fields: Any,
    ) -> AuditEvent:
        record = AuditRecordInput(event_type=event_type, entity_type=entity_type, **fields)
        if _contains_sensitive_key(record.metadata):
            raise ValueError("audit metadata contains a prohibited sensitive key")
        if organisation_id is not None:
            AuthorizationService.require_permission(context, "audit.read", organisation_id)
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc),
            actor_type=actor_type,
            actor_id=context.user_id,
            organisation_id=organisation_id,
            event_type=record.event_type,
            entity_type=record.entity_type,
            entity_id=record.entity_id,
            entity_version=record.entity_version,
            request_id=record.request_id,
            workflow_id=record.workflow_id,
            reason=record.reason,
            metadata_json=record.metadata,
            previous_state_hash=record.previous_state_hash,
            new_state_hash=record.new_state_hash,
        )
        session.add(event)
        session.flush()
        return event

    @staticmethod
    def list_for_organisation(
        session: Session, context: TenantContext, organisation_id: uuid.UUID
    ) -> list[AuditEvent]:
        AuthorizationService.require_permission(context, "audit.read", organisation_id)
        return list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.organisation_id == organisation_id)
                .order_by(AuditEvent.timestamp)
            )
        )

    @staticmethod
    def record_security(
        session: Session,
        *,
        event_type: str,
        entity_type: str,
        entity_id: Optional[str] = None,
        actor_id: Optional[uuid.UUID] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEvent:
        """Record an authentication event without requiring tenant context."""
        record = AuditRecordInput(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata=metadata or {},
        )
        if _contains_sensitive_key(record.metadata):
            raise ValueError("audit metadata contains a prohibited sensitive key")
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc),
            actor_type=ActorType.HUMAN if actor_id else ActorType.SYSTEM_SERVICE,
            actor_id=actor_id,
            event_type=record.event_type,
            entity_type=record.entity_type,
            entity_id=record.entity_id,
            metadata_json=record.metadata,
        )
        session.add(event)
        session.flush()
        return event
