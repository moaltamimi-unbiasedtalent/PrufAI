"""Shared exact execution bundles over the existing AI registries; no new registry roots."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, cast

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.ai_orchestration.service import AIOrchestrationService
from app.audit import AuditService
from app.authorization import AuthorizationError, TenantContext, build_tenant_context
from app.config import Settings
from app.models import (
    AgentDefinition,
    AgentVersion,
    AIModelProfile,
    AIWorkflowDefinition,
    AIWorkflowVersion,
    AuditEvent,
    PromptDefinition,
    PromptVersion,
    User,
)
from app.models.enums import ActorType, AIArtifactStatus
from app.models.feedback import GovernedChangeCandidate
from app.writing_assessment.service import canonical_checksum


@dataclass(frozen=True)
class ExecutionProfileSpec:
    purpose: str
    approval_event: str
    error_prefix: str
    label: str
    input_schema: dict[str, Any]
    workflow_graph: dict[str, Any]
    output_schema: Callable[[], dict[str, Any]]
    validate_configuration: Callable[[dict[str, Any], Settings], dict[str, Any]]
    native_structured_output: bool = True
    validate_approval: Callable[[dict[str, Any], Settings], None] | None = None


MODEL_FIELDS = (
    "id",
    "code",
    "provider",
    "model_name",
    "purpose",
    "configuration",
    "supports_structured_output",
    "context_window",
    "max_output_tokens",
    "created_by",
)
PROMPT_FIELDS = (
    "id",
    "prompt_definition_id",
    "version_label",
    "system_template",
    "instruction_template",
    "input_schema_version",
    "output_schema_version",
    "configuration",
    "checksum",
    "created_by",
)
AGENT_FIELDS = (
    "id",
    "agent_definition_id",
    "version_label",
    "model_profile_id",
    "prompt_version_id",
    "tool_policy",
    "input_schema",
    "output_schema",
    "checksum",
    "created_by",
)
WORKFLOW_FIELDS = (
    "id",
    "workflow_definition_id",
    "version_label",
    "graph_schema_version",
    "graph_definition",
    "checksum",
    "created_by",
)


def _safe(value: Any, error_prefix: str = "WRITING") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or (
                key
                not in {
                    "candidate_data_transfer_authorization_reference",
                    "cross_border_transfer_authorization_reference",
                }
                and any(
                    word in key.lower()
                    for word in ("secret", "password", "token", "api_key", "authorization")
                )
                and key != "max_output_tokens"
            ):
                raise ValueError(f"{error_prefix}_CONFIGURATION_CONTAINS_CREDENTIALS")
            _safe(child, error_prefix)
    elif isinstance(value, list):
        for child in value:
            _safe(child, error_prefix)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"INVALID_{error_prefix}_CONFIGURATION")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"INVALID_{error_prefix}_CONFIGURATION")


def _record(item: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        key: str(getattr(item, key))
        if isinstance(getattr(item, key), uuid.UUID)
        else copy.deepcopy(getattr(item, key))
        for key in fields
    }


def _existing_checksum(value: dict[str, Any]) -> str:
    # Preserve the existing registry service's checksum convention.
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def snapshot_execution_profile(
    session: Session,
    *,
    spec: ExecutionProfileSpec,
    model_profile_id: uuid.UUID,
    prompt_version_id: uuid.UUID,
    agent_version_id: uuid.UUID,
    workflow_version_id: uuid.UUID,
) -> dict[str, Any]:
    model = session.get(AIModelProfile, model_profile_id)
    prompt = session.get(PromptVersion, prompt_version_id)
    agent = session.get(AgentVersion, agent_version_id)
    workflow = session.get(AIWorkflowVersion, workflow_version_id)
    if any(item is None for item in (model, prompt, agent, workflow)):
        raise ValueError(f"{spec.error_prefix}_EXECUTION_PROFILE_NOT_FOUND")
    assert model and prompt and agent and workflow
    if (
        model.purpose != spec.purpose
        or prompt.definition.purpose != spec.purpose
        or agent.definition.purpose != spec.purpose
        or workflow.definition.purpose != spec.purpose
        or agent.model_profile_id != model.id
        or agent.prompt_version_id != prompt.id
        or agent.tool_policy != []
        or agent.input_schema != spec.input_schema
        or agent.output_schema != spec.output_schema()
        or workflow.graph_schema_version != "1"
        or workflow.graph_definition != spec.workflow_graph
        or model.supports_structured_output is not spec.native_structured_output
    ):
        raise ValueError(f"{spec.error_prefix}_EXECUTION_PROFILE_INCOMPATIBLE")
    _safe(model.configuration, spec.error_prefix)
    _safe(prompt.configuration, spec.error_prefix)
    if (
        not 1 <= len(prompt.system_template) <= 16000
        or len(prompt.instruction_template or "") > 16000
    ):
        raise ValueError(f"INVALID_{spec.error_prefix}_PROMPT")
    if prompt.checksum != _existing_checksum(
        {
            "system_template": prompt.system_template,
            "instruction_template": prompt.instruction_template,
            "configuration": prompt.configuration,
            "input_schema_version": prompt.input_schema_version,
            "output_schema_version": prompt.output_schema_version,
        }
    ):
        raise ValueError(f"STALE_{spec.error_prefix}_PROMPT")
    if agent.checksum != _existing_checksum(
        {
            "version_label": agent.version_label,
            "model_profile_id": str(model.id),
            "prompt_version_id": str(prompt.id),
            "tool_policy": agent.tool_policy,
            "input_schema": agent.input_schema,
            "output_schema": agent.output_schema,
        }
    ):
        raise ValueError(f"STALE_{spec.error_prefix}_AGENT")
    if workflow.checksum != _existing_checksum(
        {
            "version_label": workflow.version_label,
            "graph_schema_version": workflow.graph_schema_version,
            "graph_definition": workflow.graph_definition,
        }
    ):
        raise ValueError(f"STALE_{spec.error_prefix}_WORKFLOW")
    artifacts = {
        "model": _record(model, MODEL_FIELDS),
        "prompt": _record(prompt, PROMPT_FIELDS),
        "agent": _record(agent, AGENT_FIELDS),
        "workflow": _record(workflow, WORKFLOW_FIELDS),
    }
    return {
        "schema_version": "1",
        "execution_profile_id": str(workflow.id),
        "profile_version": canonical_checksum(artifacts),
        "model_version": canonical_checksum(artifacts["model"]),
        "model_profile_id": str(model.id),
        "prompt_version_id": str(prompt.id),
        "prompt_checksum": prompt.checksum,
        "agent_version_id": str(agent.id),
        "agent_checksum": agent.checksum,
        "workflow_version_id": str(workflow.id),
        "workflow_checksum": workflow.checksum,
        "provider": model.provider,
        "model_name": model.model_name,
        "adapter_version": str(model.configuration.get("adapter_version", "1")),
        "configuration": copy.deepcopy(model.configuration),
        "system_template": prompt.system_template,
        "instruction_template": prompt.instruction_template,
        "tool_policy": list(agent.tool_policy),
        "input_schema": copy.deepcopy(agent.input_schema),
        "output_schema": copy.deepcopy(agent.output_schema),
        "workflow_graph": copy.deepcopy(workflow.graph_definition),
        "artifacts": artifacts,
    }


def _snapshot(
    session: Session, snapshot: dict[str, Any], spec: ExecutionProfileSpec
) -> dict[str, Any]:
    try:
        return snapshot_execution_profile(
            session,
            spec=spec,
            **{
                k: uuid.UUID(str(snapshot[k]))
                for k in (
                    "model_profile_id",
                    "prompt_version_id",
                    "agent_version_id",
                    "workflow_version_id",
                )
            },
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{spec.error_prefix}_PROFILE_INVALID") from exc


def _governed_draft(session: Session, prompt_version_id: uuid.UUID) -> bool:
    return (
        session.scalar(
            select(GovernedChangeCandidate.id).where(
                GovernedChangeCandidate.draft_prompt_version_id == prompt_version_id
            )
        )
        is not None
    )


def _executable(session: Session, snapshot: dict[str, Any]) -> bool:
    try:
        if _governed_draft(session, uuid.UUID(snapshot["prompt_version_id"])):
            return False
        for cls, key in (
            (AIModelProfile, "model_profile_id"),
            (PromptVersion, "prompt_version_id"),
            (AgentVersion, "agent_version_id"),
            (AIWorkflowVersion, "workflow_version_id"),
        ):
            item = cast(Any, session.get(cls, uuid.UUID(snapshot[key])))
            if item is None or item.status not in {
                AIArtifactStatus.ACTIVE,
                AIArtifactStatus.PUBLISHED,
            }:
                return False
        return True
    except (ValueError, KeyError, TypeError):
        return False


def _approval_matches(
    record: AuditEvent, snapshot: dict[str, Any], spec: ExecutionProfileSpec
) -> bool:
    metadata = record.metadata_json or {}
    if (
        record.event_type != spec.approval_event
        or record.entity_type != "AIWorkflowVersion"
        or record.entity_id != snapshot.get("workflow_version_id")
        or record.organisation_id is not None
        or record.actor_type != ActorType.HUMAN
        or record.actor_id is None
        or metadata.get("profile_version") != snapshot.get("profile_version")
        or metadata.get("model_version") != snapshot.get("model_version")
    ):
        return False
    for key in ("model_profile_id", "prompt_version_id", "agent_version_id", "workflow_version_id"):
        if metadata.get(key) != snapshot.get(key):
            return False
    creators = [value.get("created_by") for value in snapshot["artifacts"].values()]
    if snapshot.get("provider") == "DETERMINISTIC_TEST":
        return (
            metadata.get("test_only") is True
            and metadata.get("qualification") == "AUTOMATED_TECHNICAL_TEST"
            and all(value == str(record.actor_id) for value in creators)
        )
    return (
        metadata.get("test_only") is False
        and metadata.get("qualification") == f"INDEPENDENT_{spec.error_prefix}_PROFILE_REVIEW"
        and all(value is not None and value != str(record.actor_id) for value in creators)
    )


def approved_profile_record(
    session: Session, snapshot: dict[str, Any], spec: ExecutionProfileSpec
) -> AuditEvent | None:
    current = _snapshot(session, snapshot, spec)
    if current != {
        key: value for key, value in snapshot.items() if key != "approval"
    } or not _executable(session, current):
        return None
    record = session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.event_type == spec.approval_event,
            AuditEvent.entity_type == "AIWorkflowVersion",
            AuditEvent.entity_id == snapshot["workflow_version_id"],
        )
        .order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    return record if record is not None and _approval_matches(record, snapshot, spec) else None


def snapshot_registry_valid(
    session: Session, snapshot: dict[str, Any], spec: ExecutionProfileSpec
) -> bool:
    try:
        if not _executable(session, snapshot):
            return False
        current = _snapshot(session, snapshot, spec)
        if current != {key: value for key, value in snapshot.items() if key != "approval"}:
            return False
        approval = snapshot.get("approval", {})
        record = session.get(AuditEvent, uuid.UUID(str(approval.get("id"))))
        return bool(
            record
            and _approval_matches(record, snapshot, spec)
            and str(record.actor_id) == approval.get("approved_by")
            and approval.get("profile_version") == snapshot["profile_version"]
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _qualify(
    session: Session,
    snapshot: dict[str, Any],
    operator: User,
    *,
    test: bool,
    spec: ExecutionProfileSpec,
) -> dict[str, Any]:
    if _governed_draft(session, uuid.UUID(snapshot["prompt_version_id"])):
        raise ValueError("GOVERNED_CHANGE_CANDIDATE_CANNOT_BE_QUALIFIED")
    for cls, key in (
        (AIModelProfile, "model_profile_id"),
        (PromptVersion, "prompt_version_id"),
        (AgentVersion, "agent_version_id"),
        (AIWorkflowVersion, "workflow_version_id"),
    ):
        item = cast(Any, session.get(cls, uuid.UUID(snapshot[key])))
        assert item is not None
        item.status = (
            AIArtifactStatus.ACTIVE if cls is AIModelProfile else AIArtifactStatus.PUBLISHED
        )
        if isinstance(item, (AIModelProfile, PromptVersion)):
            item.approved_at, item.approved_by = datetime.now(timezone.utc), operator.id
        if isinstance(item, PromptVersion):
            item.published_at, item.published_by = datetime.now(timezone.utc), operator.id
    metadata = {
        key: snapshot[key]
        for key in (
            "model_profile_id",
            "prompt_version_id",
            "agent_version_id",
            "workflow_version_id",
            "profile_version",
            "model_version",
        )
    }
    metadata.update(
        test_only=test,
        qualification=(
            "AUTOMATED_TECHNICAL_TEST"
            if test
            else f"INDEPENDENT_{spec.error_prefix}_PROFILE_REVIEW"
        ),
    )
    session.flush()
    record = AuditService.record(
        session,
        build_tenant_context(session, operator.id),
        event_type=spec.approval_event,
        entity_type="AIWorkflowVersion",
        entity_id=snapshot["workflow_version_id"],
        metadata=metadata,
        require_audit_permission=False,
    )
    return {
        **snapshot,
        "approval": {
            "id": str(record.id),
            "profile_version": snapshot["profile_version"],
            "approved_by": str(operator.id),
        },
    }


def qualify_test_execution_profile(
    session: Session,
    *,
    snapshot: dict[str, Any],
    operator: User,
    settings: Settings,
    spec: ExecutionProfileSpec,
) -> dict[str, Any]:
    if (
        settings.environment not in {"test", "development"}
        or snapshot.get("provider") != "DETERMINISTIC_TEST"
    ):
        raise ValueError("TEST_ONLY_PROFILE_QUALIFICATION_REQUIRED")
    context = build_tenant_context(session, operator.id)
    if not {"ai.manage", "ai.review"} <= context.platform_permissions:
        raise AuthorizationError("platform configuration authority required")
    current = _snapshot(session, snapshot, spec)
    if current != {key: value for key, value in snapshot.items() if key != "approval"} or any(
        item.get("created_by") != str(operator.id) for item in current["artifacts"].values()
    ):
        raise ValueError(f"{spec.error_prefix}_PROFILE_NAMESPACE_CONFLICT")
    record = approved_profile_record(session, current, spec)
    if record is not None:
        return {
            **current,
            "approval": {
                "id": str(record.id),
                "profile_version": current["profile_version"],
                "approved_by": str(record.actor_id),
            },
        }
    return _qualify(session, current, operator, test=True, spec=spec)


def approve_execution_profile(
    session: Session,
    *,
    spec: ExecutionProfileSpec,
    snapshot: dict[str, Any],
    context: TenantContext,
    settings: Settings,
    expected_profile_version: str,
) -> dict[str, Any]:
    if context.organisation_id is not None:
        raise AuthorizationError("platform configuration context required")
    trusted = build_tenant_context(session, context.user_id)
    if not {"ai.manage", "ai.review"} <= trusted.platform_permissions:
        raise AuthorizationError("platform configuration and review authority required")
    # Serialize approval against concurrent registry edits; refresh identity-map state.
    for cls, key in (
        (AIModelProfile, "model_profile_id"),
        (PromptVersion, "prompt_version_id"),
        (AgentVersion, "agent_version_id"),
        (AIWorkflowVersion, "workflow_version_id"),
    ):
        session.scalar(
            select(cls)
            .where(cls.id == uuid.UUID(snapshot[key]))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    current = _snapshot(session, snapshot, spec)
    if expected_profile_version != current["profile_version"]:
        raise ValueError(f"{spec.error_prefix}_PROFILE_VERSION_CONFLICT")
    if current["provider"] == "DETERMINISTIC_TEST":
        raise ValueError("TEST_PROFILE_AUTOMATED_QUALIFICATION_ONLY")
    if any(
        not item.get("created_by") or item["created_by"] == str(trusted.user_id)
        for item in current["artifacts"].values()
    ):
        raise AuthorizationError(f"independent {spec.label} profile approval required")
    availability = spec.validate_configuration(current, settings)
    if availability["availability"] == "UNCONFIGURED" and availability[
        "unavailable_reason"
    ] not in {"DEPLOYMENT_APPROVAL_REQUIRED", "CREDENTIAL_UNAVAILABLE"}:
        raise ValueError(f"{spec.error_prefix}_PROFILE_CONFIGURATION_INVALID")
    if spec.validate_approval is not None:
        spec.validate_approval(current, settings)
    existing = approved_profile_record(session, current, spec)
    if existing:
        return {
            **current,
            "approval": {
                "id": str(existing.id),
                "profile_version": current["profile_version"],
                "approved_by": str(existing.actor_id),
            },
        }
    operator = session.get(User, trusted.user_id)
    assert operator is not None
    return _qualify(session, current, operator, test=False, spec=spec)


def create_execution_profile(
    session: Session,
    *,
    spec: ExecutionProfileSpec,
    context: TenantContext,
    code: str,
    name: str,
    provider: str,
    model_name: str,
    configuration: dict[str, Any],
    system_template: str,
    instruction_template: str | None = None,
) -> dict[str, Any]:
    trusted = build_tenant_context(session, context.user_id)
    if context.organisation_id is not None or "ai.manage" not in trusted.platform_permissions:
        raise AuthorizationError("platform AI configuration authority required")
    if not 3 <= len(code) <= 100 or not 1 <= len(name) <= 200:
        raise ValueError(f"INVALID_{spec.error_prefix}_PROFILE_NAME")
    _safe(configuration, spec.error_prefix)
    model = session.scalar(select(AIModelProfile).where(AIModelProfile.code == code))
    if model is not None:
        prompt_def = session.scalar(
            select(PromptDefinition).where(PromptDefinition.code == code + ".prompt")
        )
        agent_def = session.scalar(
            select(AgentDefinition).where(AgentDefinition.code == code + ".agent")
        )
        workflow_def = session.scalar(
            select(AIWorkflowDefinition).where(AIWorkflowDefinition.code == code + ".workflow")
        )
        if not prompt_def or not agent_def or not workflow_def:
            raise ValueError(f"{spec.error_prefix}_PROFILE_NAMESPACE_CONFLICT")
        prompt = session.scalar(
            select(PromptVersion).where(
                PromptVersion.prompt_definition_id == prompt_def.id,
                PromptVersion.version_label == "1",
            )
        )
        agent = session.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_definition_id == agent_def.id, AgentVersion.version_label == "1"
            )
        )
        workflow = session.scalar(
            select(AIWorkflowVersion).where(
                AIWorkflowVersion.workflow_definition_id == workflow_def.id,
                AIWorkflowVersion.version_label == "1",
            )
        )
        if (
            not prompt
            or not agent
            or not workflow
            or model.created_by != trusted.user_id
            or model.name != name
            or model.provider != provider
            or model.model_name != model_name
            or model.configuration != configuration
            or prompt.system_template != system_template
            or prompt.instruction_template != instruction_template
        ):
            raise ValueError(f"{spec.error_prefix}_PROFILE_NAMESPACE_CONFLICT")
    else:
        service = AIOrchestrationService
        model = service.create_model(
            session,
            code=code,
            name=name,
            provider=provider,
            model_name=model_name,
            purpose=spec.purpose,
            configuration=configuration,
            context=trusted,
        )
        model.supports_structured_output = spec.native_structured_output
        model.created_by = trusted.user_id
        prompt_def = service.create_prompt(
            session, code=code + ".prompt", name=name, purpose=spec.purpose, context=trusted
        )
        prompt = service.create_prompt_version(
            session,
            prompt_def,
            version_label="1",
            system_template=system_template,
            instruction_template=instruction_template,
            context=trusted,
        )
        prompt.created_by = trusted.user_id
        agent_def = service.create_agent(
            session, code=code + ".agent", name=name, purpose=spec.purpose, context=trusted
        )
        agent = service.create_agent_version(
            session,
            agent_def,
            version_label="1",
            model_profile_id=model.id,
            prompt_version_id=prompt.id,
            tool_policy=[],
            input_schema=spec.input_schema,
            output_schema=spec.output_schema(),
            context=trusted,
        )
        agent.created_by = trusted.user_id
        workflow_def = service.create_workflow(
            session, code=code + ".workflow", name=name, purpose=spec.purpose, context=trusted
        )
        workflow = service.create_workflow_version(
            session,
            workflow_def,
            version_label="1",
            graph_definition=spec.workflow_graph,
            context=trusted,
        )
        workflow.created_by = trusted.user_id
        session.flush()
    return snapshot_execution_profile(
        session,
        spec=spec,
        model_profile_id=model.id,
        prompt_version_id=prompt.id,
        agent_version_id=agent.id,
        workflow_version_id=workflow.id,
    )


@event.listens_for(Session, "before_commit")
@event.listens_for(Session, "before_flush")
def _protect_approved_execution_registry(
    session: Session, _context: Any = None, _instances: Any = None
) -> None:
    kinds: dict[type[Any], tuple[str, tuple[str, ...]]] = {
        AIModelProfile: ("model_profile_id", MODEL_FIELDS),
        PromptVersion: ("prompt_version_id", PROMPT_FIELDS),
        AgentVersion: ("agent_version_id", AGENT_FIELDS),
        AIWorkflowVersion: ("workflow_version_id", WORKFLOW_FIELDS),
    }
    for candidate in list(session.identity_map.values()) + list(session.deleted):
        item = cast(Any, candidate)
        entry = kinds.get(type(item))
        if entry is None:
            continue
        key, fields = entry
        stored = (
            session.connection()
            .execute(select(item.__table__).where(item.__table__.c.id == item.id))
            .mappings()
            .first()
        )
        if stored is None:
            continue
        approved = (
            session.connection()
            .execute(
                select(AuditEvent.id)
                .where(
                    AuditEvent.event_type.in_(
                        (
                            "writing_execution.profile_approved",
                            "speaking_execution.profile_approved",
                        )
                    ),
                    AuditEvent.metadata_json[key].as_string() == str(item.id),
                )
                .limit(1)
            )
            .first()
        )
        if approved is None:
            continue
        if item in session.deleted:
            raise ValueError("approved execution registry history cannot be deleted")
        for field in fields:
            if stored[field] != getattr(item, field):
                raise ValueError("approved execution registry content is immutable")
