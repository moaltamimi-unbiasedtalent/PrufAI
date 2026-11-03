from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import AuditService
from app.authorization import TenantContext
from app.models import (
    AgentDefinition,
    AgentVersion,
    AIModelProfile,
    AIRun,
    AIWorkflowCheckpoint,
    AIWorkflowDefinition,
    AIWorkflowVersion,
    HumanReviewRequest,
    PromptDefinition,
    PromptVersion,
)
from app.models.enums import AIArtifactStatus, AIRunStatus, HumanReviewStatus


def _checksum(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class AIOrchestrationService:
    ALLOWED_TOOLS = {
        "KNOWLEDGE_RETRIEVAL",
        "STANDARD_PACK_READ",
        "RULES_EVALUATE",
        "CITATION_RESOLVE",
        "RUBRIC_READ",
    }

    @staticmethod
    def create_model(
        session: Session,
        *,
        code: str,
        name: str,
        provider: str,
        model_name: str,
        purpose: str,
        configuration: Optional[dict[str, Any]] = None,
        context: Optional[TenantContext] = None,
    ) -> AIModelProfile:
        item = AIModelProfile(
            code=code,
            name=name,
            provider=provider,
            model_name=model_name,
            purpose=purpose,
            status=AIArtifactStatus.DRAFT,
            configuration=configuration or {},
            supports_structured_output=True,
        )
        session.add(item)
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.model.created", item)
        return item

    @staticmethod
    def create_prompt(
        session: Session,
        *,
        code: str,
        name: str,
        purpose: str,
        context: Optional[TenantContext] = None,
    ) -> PromptDefinition:
        item = PromptDefinition(
            code=code, name=name, purpose=purpose, status=AIArtifactStatus.DRAFT
        )
        session.add(item)
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.prompt.created", item)
        return item

    @staticmethod
    def create_prompt_version(
        session: Session,
        definition: PromptDefinition,
        *,
        version_label: str,
        system_template: str,
        instruction_template: Optional[str] = None,
        configuration: Optional[dict[str, Any]] = None,
        context: Optional[TenantContext] = None,
    ) -> PromptVersion:
        payload = {
            "system_template": system_template,
            "instruction_template": instruction_template,
            "configuration": configuration or {},
            "input_schema_version": "1",
            "output_schema_version": "1",
        }
        item = PromptVersion(
            prompt_definition_id=definition.id,
            version_label=version_label,
            **payload,
            status=AIArtifactStatus.DRAFT,
            checksum=_checksum(payload),
        )
        session.add(item)
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.prompt.version.created", item)
        return item

    @staticmethod
    def create_agent(
        session: Session,
        *,
        code: str,
        name: str,
        purpose: str,
        context: Optional[TenantContext] = None,
    ) -> AgentDefinition:
        item = AgentDefinition(code=code, name=name, purpose=purpose, status=AIArtifactStatus.DRAFT)
        session.add(item)
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.agent.created", item)
        return item

    @staticmethod
    def create_agent_version(
        session: Session,
        definition: AgentDefinition,
        *,
        version_label: str,
        model_profile_id: uuid.UUID,
        prompt_version_id: uuid.UUID,
        tool_policy: list[str],
        input_schema: dict[str, Any],
        output_schema: dict[str, Any],
        context: Optional[TenantContext] = None,
    ) -> AgentVersion:
        if any(tool not in AIOrchestrationService.ALLOWED_TOOLS for tool in tool_policy):
            raise ValueError("agent tool is not allow-listed")
        payload = {
            "version_label": version_label,
            "model_profile_id": str(model_profile_id),
            "prompt_version_id": str(prompt_version_id),
            "tool_policy": tool_policy,
            "input_schema": input_schema,
            "output_schema": output_schema,
        }
        item = AgentVersion(
            agent_definition_id=definition.id,
            model_profile_id=model_profile_id,
            prompt_version_id=prompt_version_id,
            version_label=version_label,
            tool_policy=tool_policy,
            input_schema=input_schema,
            output_schema=output_schema,
            status=AIArtifactStatus.DRAFT,
            checksum=_checksum(payload),
        )
        session.add(item)
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.agent.version.created", item)
        return item

    @staticmethod
    def create_workflow(
        session: Session,
        *,
        code: str,
        name: str,
        purpose: str,
        context: Optional[TenantContext] = None,
    ) -> AIWorkflowDefinition:
        item = AIWorkflowDefinition(
            code=code, name=name, purpose=purpose, status=AIArtifactStatus.DRAFT
        )
        session.add(item)
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.workflow.created", item)
        return item

    @staticmethod
    def create_workflow_version(
        session: Session,
        definition: AIWorkflowDefinition,
        *,
        version_label: str,
        graph_definition: dict[str, Any],
        context: Optional[TenantContext] = None,
    ) -> AIWorkflowVersion:
        allowed_nodes = {
            "INPUT",
            "RETRIEVAL",
            "PROPOSAL",
            "REVIEW",
            "RULE_CHECK",
            "DECISION",
            "HITL",
        }
        nodes = graph_definition.get("nodes", [])
        if not isinstance(nodes, list) or any(
            node.get("type") not in allowed_nodes for node in nodes if isinstance(node, dict)
        ):
            raise ValueError("workflow contains an unapproved node type")
        payload = {
            "version_label": version_label,
            "graph_schema_version": "1",
            "graph_definition": graph_definition,
        }
        item = AIWorkflowVersion(
            workflow_definition_id=definition.id,
            version_label=version_label,
            graph_schema_version="1",
            graph_definition=graph_definition,
            status=AIArtifactStatus.DRAFT,
            checksum=_checksum(payload),
        )
        session.add(item)
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.workflow.version.created", item)
        return item

    @staticmethod
    def ensure_mutable(item: Any) -> None:
        if item.status in {
            AIArtifactStatus.PUBLISHED,
            AIArtifactStatus.ACTIVE,
            AIArtifactStatus.DEPRECATED,
        }:
            raise ValueError("published AI artifact is immutable")

    @staticmethod
    def start_run(
        session: Session,
        workflow: AIWorkflowVersion,
        *,
        request_id: str,
        initiated_by_user_id: Optional[uuid.UUID] = None,
        context: Optional[TenantContext] = None,
    ) -> AIRun:
        if workflow.status not in {
            AIArtifactStatus.APPROVED,
            AIArtifactStatus.PUBLISHED,
            AIArtifactStatus.ACTIVE,
        }:
            raise ValueError("workflow version is not executable")
        item = AIRun(
            workflow_version_id=workflow.id,
            request_id=request_id,
            initiated_by_user_id=initiated_by_user_id,
            status=AIRunStatus.RUNNING,
        )
        session.add(item)
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.run.created", item)
        AIOrchestrationService._audit(session, context, "ai.workflow.run.started", item)
        return item

    @staticmethod
    def complete_run(
        session: Session, run: AIRun, context: Optional[TenantContext] = None
    ) -> AIRun:
        if run.status is not AIRunStatus.RUNNING:
            raise ValueError("run is not running")
        run.status = AIRunStatus.SUCCEEDED
        run.completed_at = datetime.now(timezone.utc)
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.workflow.run.completed", run)
        return run

    @staticmethod
    def fail_run(
        session: Session,
        run: AIRun,
        failure_category: str,
        context: Optional[TenantContext] = None,
    ) -> AIRun:
        if run.status in {AIRunStatus.SUCCEEDED, AIRunStatus.CANCELLED, AIRunStatus.REJECTED}:
            raise ValueError("run is already terminal")
        run.status = AIRunStatus.FAILED
        run.failure_category = failure_category
        run.completed_at = datetime.now(timezone.utc)
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.workflow.run.failed", run)
        return run

    @staticmethod
    def request_review(
        session: Session,
        run: AIRun,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        requested_by: Optional[uuid.UUID] = None,
        checksum: Optional[str] = None,
        context: Optional[TenantContext] = None,
    ) -> HumanReviewRequest:
        if run.status not in {AIRunStatus.RUNNING, AIRunStatus.WAITING_FOR_HUMAN}:
            raise ValueError("run is not reviewable")
        run.status = AIRunStatus.WAITING_FOR_HUMAN
        item = HumanReviewRequest(
            review_type="AI_PROPOSAL",
            entity_type=entity_type,
            entity_id=entity_id,
            ai_run_id=run.id,
            status=HumanReviewStatus.PENDING,
            requested_by=requested_by,
            requested_at=datetime.now(timezone.utc),
            reviewed_checksum=checksum,
        )
        session.add(item)
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.review.requested", item)
        return item

    @staticmethod
    def decide_review(
        session: Session,
        review: HumanReviewRequest,
        *,
        decision: HumanReviewStatus,
        actor_id: uuid.UUID,
        checksum: Optional[str] = None,
        reason: Optional[str] = None,
        context: Optional[TenantContext] = None,
    ) -> HumanReviewRequest:
        if review.review_type in {"RUBRIC_PUBLICATION", "HUMAN_WRITING_ASSESSMENT"}:
            raise ValueError("review requires a domain governed decision")
        if review.status not in {HumanReviewStatus.PENDING, HumanReviewStatus.IN_REVIEW}:
            raise ValueError("review already decided")
        if review.reviewed_checksum and checksum and review.reviewed_checksum != checksum:
            raise ValueError("stale review artifact")
        review.status, review.decided_by, review.decided_at, review.decision, review.reason = (
            decision,
            actor_id,
            datetime.now(timezone.utc),
            decision.value,
            reason,
        )
        if review.ai_run_id:
            run = session.get(AIRun, review.ai_run_id)
            if run:
                run.status = (
                    AIRunStatus.WAITING_FOR_HUMAN
                    if decision is HumanReviewStatus.APPROVED
                    else AIRunStatus.REJECTED
                    if decision is HumanReviewStatus.REJECTED
                    else AIRunStatus.WAITING_FOR_HUMAN
                )
        events = {
            HumanReviewStatus.APPROVED: "ai.review.approved",
            HumanReviewStatus.REJECTED: "ai.review.rejected",
            HumanReviewStatus.CHANGES_REQUESTED: "ai.review.changes_requested",
            HumanReviewStatus.CANCELLED: "ai.review.cancelled",
        }
        AIOrchestrationService._audit(session, context, events[decision], review)
        session.flush()
        return review

    @staticmethod
    def resume_run(
        session: Session,
        review: HumanReviewRequest,
        *,
        actor_id: uuid.UUID,
        context: Optional[TenantContext] = None,
    ) -> AIRun:
        if review.status is not HumanReviewStatus.APPROVED:
            raise ValueError("only approved reviews can resume")
        if review.ai_run_id is None:
            raise ValueError("review has no run")
        run = session.get(AIRun, review.ai_run_id)
        if run is None or run.status is not AIRunStatus.WAITING_FOR_HUMAN:
            raise ValueError("run is not waiting for resume")
        run.status = AIRunStatus.RUNNING
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.workflow.resumed", run)
        return run

    @staticmethod
    def cancel_run(session: Session, run: AIRun, context: Optional[TenantContext] = None) -> AIRun:
        if run.status is AIRunStatus.CANCELLED:
            return run
        if run.status not in {
            AIRunStatus.PENDING,
            AIRunStatus.RUNNING,
            AIRunStatus.WAITING_FOR_HUMAN,
        }:
            raise ValueError("run is not cancellable")
        run.status = AIRunStatus.CANCELLED
        session.query(AIWorkflowCheckpoint).filter(AIWorkflowCheckpoint.run_id == run.id).update(
            {"status": "CANCELLED"}
        )
        session.flush()
        AIOrchestrationService._audit(session, context, "ai.workflow.run.cancelled", run)
        return run

    @staticmethod
    def save_checkpoint(
        session: Session,
        run: AIRun,
        workflow_version: AIWorkflowVersion,
        *,
        node_id: str,
        state: dict[str, Any],
        organisation_id: Optional[uuid.UUID] = None,
    ) -> AIWorkflowCheckpoint:
        if run.status is not AIRunStatus.WAITING_FOR_HUMAN:
            raise ValueError("checkpoint requires a human-waiting run")
        checkpoint = session.scalar(
            select(AIWorkflowCheckpoint).where(AIWorkflowCheckpoint.run_id == run.id)
        )
        if checkpoint is None:
            checkpoint = AIWorkflowCheckpoint(
                run_id=run.id,
                workflow_version_id=workflow_version.id,
                organisation_id=organisation_id,
                node_id=node_id,
                state=state,
                status="ACTIVE",
            )
            session.add(checkpoint)
        else:
            checkpoint.node_id, checkpoint.state, checkpoint.status = node_id, state, "ACTIVE"
        session.flush()
        return checkpoint

    @staticmethod
    def load_checkpoint(
        session: Session, run: AIRun, *, organisation_id: Optional[uuid.UUID] = None
    ) -> AIWorkflowCheckpoint:
        checkpoint = session.scalar(
            select(AIWorkflowCheckpoint).where(AIWorkflowCheckpoint.run_id == run.id)
        )
        if checkpoint is None or checkpoint.organisation_id != organisation_id:
            raise ValueError("checkpoint is not available for this run context")
        if run.status in {AIRunStatus.CANCELLED, AIRunStatus.SUCCEEDED, AIRunStatus.REJECTED}:
            raise ValueError("terminal run cannot resume from checkpoint")
        return checkpoint

    @staticmethod
    def cleanup_checkpoints(session: Session) -> int:
        result = (
            session.query(AIWorkflowCheckpoint)
            .filter(AIWorkflowCheckpoint.status != "ACTIVE")
            .delete()
        )
        session.flush()
        return result

    @staticmethod
    def _audit(session: Session, context: Optional[TenantContext], event: str, entity: Any) -> None:
        if context is not None:
            AuditService.record(
                session,
                context,
                event_type=event,
                entity_type="ai_orchestration",
                entity_id=str(entity.id),
            )
