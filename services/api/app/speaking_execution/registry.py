"""AUDIO-only Speaking contract over the shared existing AI registries."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.ai_orchestration import execution_profiles as core
from app.authorization import TenantContext
from app.config import Settings
from app.models import AuditEvent, User
from app.speaking_execution.provider import build_output_schema
from app.speaking_execution.runtime import (
    validate_execution_configuration,
    validate_profile_approval,
)

PURPOSE = "SPEAKING_ASSESSMENT"
APPROVAL_EVENT = "speaking_execution.profile_approved"
WORKFLOW_GRAPH = {
    "nodes": [
        {"id": "speaking_input", "type": "INPUT"},
        {"id": "speaking_proposal", "type": "PROPOSAL"},
        {"id": "speaking_validation", "type": "RULE_CHECK"},
        {"id": "speaking_review", "type": "HITL"},
    ]
}
INPUT_SCHEMA = {
    "schema_version": "1",
    "purpose": PURPOSE,
    "evidence_mode": "AUDIO",
    "candidate_audio_untrusted": True,
    "transcript_allowed": False,
}
SPEC = core.ExecutionProfileSpec(
    PURPOSE,
    APPROVAL_EVENT,
    "SPEAKING",
    "Speaking",
    INPUT_SCHEMA,
    WORKFLOW_GRAPH,
    build_output_schema,
    validate_execution_configuration,
    native_structured_output=False,
    validate_approval=validate_profile_approval,
)


def snapshot_execution_profile(
    session: Session,
    *,
    model_profile_id: uuid.UUID,
    prompt_version_id: uuid.UUID,
    agent_version_id: uuid.UUID,
    workflow_version_id: uuid.UUID,
) -> dict[str, Any]:
    return core.snapshot_execution_profile(
        session,
        spec=SPEC,
        model_profile_id=model_profile_id,
        prompt_version_id=prompt_version_id,
        agent_version_id=agent_version_id,
        workflow_version_id=workflow_version_id,
    )


def approved_profile_record(session: Session, snapshot: dict[str, Any]) -> AuditEvent | None:
    return core.approved_profile_record(session, snapshot, SPEC)


def snapshot_registry_valid(session: Session, snapshot: dict[str, Any]) -> bool:
    return core.snapshot_registry_valid(session, snapshot, SPEC)


def qualify_test_execution_profile(
    session: Session, *, snapshot: dict[str, Any], operator: User, settings: Settings
) -> dict[str, Any]:
    return core.qualify_test_execution_profile(
        session, spec=SPEC, snapshot=snapshot, operator=operator, settings=settings
    )


def approve_execution_profile(
    session: Session,
    *,
    snapshot: dict[str, Any],
    context: TenantContext,
    settings: Settings,
    expected_profile_version: str,
) -> dict[str, Any]:
    return core.approve_execution_profile(
        session,
        spec=SPEC,
        snapshot=snapshot,
        context=context,
        settings=settings,
        expected_profile_version=expected_profile_version,
    )


def create_execution_profile(
    session: Session,
    *,
    context: TenantContext,
    code: str,
    name: str,
    provider: str,
    model_name: str,
    configuration: dict[str, Any],
    system_template: str,
    instruction_template: str | None = None,
) -> dict[str, Any]:
    return core.create_execution_profile(
        session,
        spec=SPEC,
        context=context,
        code=code,
        name=name,
        provider=provider,
        model_name=model_name,
        configuration=configuration,
        system_template=system_template,
        instruction_template=instruction_template,
    )
