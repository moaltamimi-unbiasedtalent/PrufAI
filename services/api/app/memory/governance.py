from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.authorization import AuthorizationService, TenantContext
from app.memory.service import canonical_checksum
from app.models import LongTermMemory, LongTermMemoryVersion
from app.models.enums import (
    MemoryCategory,
    MemoryLifecycleStatus,
    MemoryOriginType,
    MemoryScopeType,
)

ALLOWED_CONTENT_KEYS = {
    "summary",
    "guidance",
    "tags",
    "applicable_context",
    "exceptions",
    "limitations",
}
IMMUTABLE = {
    MemoryLifecycleStatus.ACTIVE,
    MemoryLifecycleStatus.RETIRED,
    MemoryLifecycleStatus.SUPERSEDED,
    MemoryLifecycleStatus.EXPIRED,
}


def validate_content(content: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(content, dict) or not content or set(content) - ALLOWED_CONTENT_KEYS:
        raise ValueError("memory content has an unsupported schema")
    if len(canonical_checksum(content)) != 64:
        raise ValueError("invalid memory checksum")
    if any(isinstance(value, str) and len(value) > 4000 for value in content.values()):
        raise ValueError("memory content field exceeds bound")
    if len(content.get("tags", [])) > 30:
        raise ValueError("too many memory tags")
    return content


class GovernedMemoryService:
    def create_memory(
        self,
        session: Session,
        context: TenantContext,
        *,
        code: str,
        name: str,
        category: MemoryCategory,
        scope_type: MemoryScopeType,
        organisation_id: uuid.UUID | None = None,
        description: str | None = None,
    ) -> LongTermMemory:
        AuthorizationService.require_permission(context, "memory.manage", organisation_id)
        if scope_type is MemoryScopeType.PLATFORM_SHARED:
            organisation_id = None
        elif organisation_id != context.organisation_id:
            raise ValueError("organisation scope mismatch")
        memory = LongTermMemory(
            code=code,
            name=name,
            category=category,
            scope_type=scope_type,
            organisation_id=organisation_id,
            description=description,
        )
        session.add(memory)
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="memory.created",
            entity_type="long_term_memory",
            entity_id=str(memory.id),
            organisation_id=organisation_id,
            metadata={"code": code, "scope": scope_type.value},
        )
        return memory

    def create_version(
        self,
        session: Session,
        context: TenantContext,
        memory: LongTermMemory,
        *,
        version_label: str,
        content: dict[str, Any],
        origin_type: MemoryOriginType = MemoryOriginType.HUMAN_AUTHORED,
        effective_from: datetime | None = None,
        expires_at: datetime | None = None,
        review_after: datetime | None = None,
        proposed_by_ai_run_id: uuid.UUID | None = None,
        supersedes_version_id: uuid.UUID | None = None,
    ) -> LongTermMemoryVersion:
        AuthorizationService.require_permission(context, "memory.manage", memory.organisation_id)
        validate_content(content)
        now = effective_from or datetime.now(timezone.utc)
        if origin_type is MemoryOriginType.AI_PROPOSED:
            status = MemoryLifecycleStatus.REVIEW_REQUIRED
        else:
            status = MemoryLifecycleStatus.DRAFT
        version = LongTermMemoryVersion(
            memory_id=memory.id,
            version_label=version_label,
            content=content,
            status=status,
            effective_from=now,
            expires_at=expires_at,
            review_after=review_after,
            origin_type=origin_type,
            proposed_by_user_id=context.user_id
            if origin_type is not MemoryOriginType.AI_PROPOSED
            else None,
            proposed_by_ai_run_id=proposed_by_ai_run_id,
            created_by_user_id=context.user_id,
            supersedes_version_id=supersedes_version_id,
            checksum=canonical_checksum(content),
        )
        session.add(version)
        session.flush()
        AuditService.record(
            session,
            context,
            event_type="memory.version.created",
            entity_type="long_term_memory_version",
            entity_id=str(version.id),
            organisation_id=memory.organisation_id,
            metadata={"checksum": version.checksum, "origin": origin_type.value},
        )
        return version

    def transition(
        self,
        session: Session,
        context: TenantContext,
        version: LongTermMemoryVersion,
        target: MemoryLifecycleStatus,
        *,
        checksum: str | None = None,
    ) -> LongTermMemoryVersion:
        memory = session.get(LongTermMemory, version.memory_id)
        if memory is None:
            raise ValueError("memory not found")
        if target is MemoryLifecycleStatus.APPROVED:
            AuthorizationService.require_permission(
                context, "memory.review", memory.organisation_id
            )
            if version.status is not MemoryLifecycleStatus.REVIEW_REQUIRED or (
                checksum and checksum != version.checksum
            ):
                raise ValueError("stale or non-reviewable memory")
            version.approved_by_user_id = context.user_id
            version.approved_at = datetime.now(timezone.utc)
        elif target is MemoryLifecycleStatus.ACTIVE:
            AuthorizationService.require_permission(
                context, "memory.activate", memory.organisation_id
            )
            if version.status is not MemoryLifecycleStatus.APPROVED:
                raise ValueError("only approved memory can activate")
            version.activated_at = datetime.now(timezone.utc)
        elif target is MemoryLifecycleStatus.REVIEW_REQUIRED:
            AuthorizationService.require_permission(
                context, "memory.manage", memory.organisation_id
            )
        elif target is MemoryLifecycleStatus.RETIRED:
            AuthorizationService.require_permission(
                context, "memory.manage", memory.organisation_id
            )
        else:
            raise ValueError("unsupported lifecycle transition")
        if version.status in IMMUTABLE and target is not MemoryLifecycleStatus.RETIRED:
            raise ValueError("active memory is immutable")
        version.status = target
        session.flush()
        AuditService.record(
            session,
            context,
            event_type=f"memory.{target.value.lower()}",
            entity_type="long_term_memory_version",
            entity_id=str(version.id),
            organisation_id=memory.organisation_id,
            metadata={"checksum": version.checksum},
        )
        return version

    def resolve(
        self,
        session: Session,
        context: TenantContext,
        *,
        category: MemoryCategory | None = None,
        now: datetime | None = None,
        limit: int = 50,
    ) -> list[LongTermMemoryVersion]:
        at = now or datetime.now(timezone.utc)
        query = (
            select(LongTermMemoryVersion)
            .join(LongTermMemory)
            .where(
                LongTermMemoryVersion.status == MemoryLifecycleStatus.ACTIVE,
                LongTermMemoryVersion.effective_from <= at,
                (LongTermMemoryVersion.effective_until.is_(None))
                | (LongTermMemoryVersion.effective_until > at),
                (LongTermMemoryVersion.expires_at.is_(None))
                | (LongTermMemoryVersion.expires_at > at),
                (LongTermMemory.scope_type == MemoryScopeType.PLATFORM_SHARED)
                | (LongTermMemory.organisation_id == context.organisation_id),
            )
        )
        if category:
            query = query.where(LongTermMemory.category == category)
        return list(
            session.scalars(
                query.order_by(LongTermMemoryVersion.effective_from.desc()).limit(min(limit, 50))
            )
        )

    def retrieve(
        self, session: Session, context: TenantContext, **kwargs: Any
    ) -> list[LongTermMemoryVersion]:
        return self.resolve(session, context, **kwargs)
