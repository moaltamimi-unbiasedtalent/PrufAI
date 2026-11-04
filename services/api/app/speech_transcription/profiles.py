"""Narrow registry approval; deployment processing approval remains separate."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.config import Settings
from app.models import AIModelProfile, AuditEvent
from app.models.enums import ActorType, AIArtifactStatus
from app.speech_transcription.execution import ExecutionError, snapshot_profile
from app.speech_transcription.runtime import validate_profile_configuration


def profile_descriptor(profile: AIModelProfile) -> dict[str, Any]:
    if profile.purpose != "SPEECH_TRANSCRIPTION":
        return {}
    try:
        languages = profile.configuration.get("languages", [])
        if not isinstance(languages, list) or not languages:
            return {"transcription_profile_version": None}
        snapshot = snapshot_profile(profile, languages[0], {})
        return {"transcription_profile_version": snapshot["profile_version"]}
    except (ExecutionError, TypeError, ValueError, AttributeError):
        return {"transcription_profile_version": None}


def _record_matches(record: AuditEvent, snapshot: dict[str, Any]) -> bool:
    metadata = record.metadata_json or {}
    if (
        record.event_type != "speech_transcription.profile_approved"
        or record.entity_type != "AIModelProfile"
        or record.entity_id != snapshot.get("profile_id")
        or record.organisation_id is not None
        or record.actor_type != ActorType.HUMAN
        or metadata.get("profile_id") != snapshot.get("profile_id")
        or metadata.get("profile_version") != snapshot.get("profile_version")
        or metadata.get("provider") != snapshot.get("provider")
        or metadata.get("adapter_version") != snapshot.get("adapter_version")
    ):
        return False
    try:
        creator = str(uuid.UUID(str(metadata.get("created_by"))))
        reviewer = str(uuid.UUID(str(record.actor_id)))
    except (ValueError, TypeError, AttributeError):
        return False
    if snapshot.get("provider") == "DETERMINISTIC_TEST":
        return (
            snapshot.get("model_name") == "fixture-transcriber"
            and metadata.get("test_only") is True
            and metadata.get("qualification") == "AUTOMATED_TECHNICAL_TEST"
        )
    return (
        creator != reviewer
        and metadata.get("test_only") is False
        and metadata.get("qualification") == "INDEPENDENT_PROFILE_REVIEW"
    )


def approved_profile_record(
    session: Session, profile: AIModelProfile, snapshot: dict[str, Any]
) -> AuditEvent | None:
    """Only the latest exact registry approval authorizes a new execution."""
    if (
        profile.status != AIArtifactStatus.ACTIVE
        or profile.approved_by is None
        or profile.approved_at is None
        or profile.created_by is None
    ):
        return None
    record = session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.event_type == "speech_transcription.profile_approved",
            AuditEvent.entity_type == "AIModelProfile",
            AuditEvent.entity_id == str(profile.id),
        )
        .order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    if (
        record is None
        or not _record_matches(record, snapshot)
        or str(record.actor_id) != str(profile.approved_by)
        or (record.metadata_json or {}).get("created_by") != str(profile.created_by)
    ):
        return None
    return record


def snapshot_approval_valid(session: Session, snapshot: dict[str, Any]) -> bool:
    """Recovery uses the pinned approval, never the mutable current profile."""
    approval = snapshot.get("approval")
    if not isinstance(approval, dict) or approval.get("profile_version") != snapshot.get(
        "profile_version"
    ):
        return False
    try:
        identifier = uuid.UUID(str(approval.get("id")))
    except (ValueError, TypeError, AttributeError):
        return False
    record = session.get(AuditEvent, identifier)
    return (
        record is not None
        and _record_matches(record, snapshot)
        and str(record.actor_id) == approval.get("approved_by")
    )


def approve_profile(
    session: Session,
    *,
    profile_id: uuid.UUID,
    expected_version: str,
    context: TenantContext,
    settings: Settings,
) -> AIModelProfile:
    if context.organisation_id is not None:
        raise AuthorizationError("platform profile approval requires platform context")
    trusted = build_tenant_context(session, context.user_id)
    if not {"ai.manage", "ai.review"} <= trusted.platform_permissions:
        raise AuthorizationError("platform AI management and review authority required")
    profile = session.scalar(
        select(AIModelProfile)
        .where(AIModelProfile.id == profile_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if profile is None or profile.purpose != "SPEECH_TRANSCRIPTION":
        raise ValueError("TRANSCRIPTION_PROFILE_NOT_FOUND")
    if profile.created_by is None or profile.created_by == trusted.user_id:
        raise AuthorizationError("independent transcription profile approval required")
    descriptor = profile_descriptor(profile)
    if descriptor.get("transcription_profile_version") != expected_version:
        raise ValueError("TRANSCRIPTION_PROFILE_VERSION_CONFLICT")
    config = profile.configuration
    snapshot = snapshot_profile(profile, config["languages"][0], {})
    if snapshot["provider"] == "DETERMINISTIC_TEST":
        raise ValueError("TEST_PROFILE_AUTOMATED_APPROVAL_ONLY")
    availability = validate_profile_configuration(snapshot, settings)
    # Registry approval does not authorize external transfer. Exact deployment
    # allowlisting and a credential must still exist when a request/worker runs.
    if availability["availability"] == "UNCONFIGURED" and availability[
        "unavailable_reason"
    ] not in {
        "DEPLOYMENT_APPROVAL_REQUIRED",
        "CREDENTIAL_UNAVAILABLE",
    }:
        raise ValueError("TRANSCRIPTION_PROFILE_CONFIGURATION_INVALID")
    if profile.status == AIArtifactStatus.ACTIVE and profile.approved_at is not None:
        if profile.approved_by != trusted.user_id:
            raise ValueError("TRANSCRIPTION_PROFILE_ALREADY_APPROVED")
        prior = approved_profile_record(session, profile, snapshot)
        if prior is None:
            raise ValueError("TRANSCRIPTION_PROFILE_VERSION_CONFLICT")
        return profile
    if profile.status != AIArtifactStatus.DRAFT:
        raise ValueError("TRANSCRIPTION_PROFILE_MUST_BE_DRAFT")
    profile.status = AIArtifactStatus.ACTIVE
    profile.approved_at, profile.approved_by = datetime.now(timezone.utc), trusted.user_id
    AuditService.record(
        session,
        trusted,
        event_type="speech_transcription.profile_approved",
        entity_type="AIModelProfile",
        entity_id=str(profile.id),
        metadata={
            "profile_id": str(profile.id),
            "profile_version": expected_version,
            "provider": snapshot["provider"],
            "adapter_version": snapshot["adapter_version"],
            "created_by": str(profile.created_by),
            "test_only": False,
            "qualification": "INDEPENDENT_PROFILE_REVIEW",
        },
        require_audit_permission=False,
    )
    session.flush()
    return profile
