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
from app.models.enums import AIArtifactStatus
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
        prior = session.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == "speech_transcription.profile_approved",
                AuditEvent.entity_id == str(profile.id),
                AuditEvent.metadata_json["profile_version"].as_string() == expected_version,
            )
        )
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
        },
        require_audit_permission=False,
    )
    session.flush()
    return profile
