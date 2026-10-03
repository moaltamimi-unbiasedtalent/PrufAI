from __future__ import annotations

import uuid

import pytest

from app.ai_orchestration.service import AIOrchestrationService
from app.authorization import TenantContext
from app.models import AuditEvent
from app.models.enums import AIArtifactStatus, HumanReviewStatus


def _platform_context() -> TenantContext:
    return TenantContext(
        user_id=uuid.uuid4(),
        organisation_id=None,
        membership_id=None,
        platform_permissions=frozenset({"ai.run", "ai.review"}),
    )


def test_registries_pin_versions_and_review_is_domain_owned(session):
    model = AIOrchestrationService.create_model(
        session,
        code="test.model",
        name="Deterministic",
        provider="TEST",
        model_name="deterministic-v1",
        purpose="synthetic",
    )
    prompt = AIOrchestrationService.create_prompt(
        session, code="test.prompt", name="Prompt", purpose="test"
    )
    prompt_version = AIOrchestrationService.create_prompt_version(
        session, prompt, version_label="1", system_template="You are a test model."
    )
    agent = AIOrchestrationService.create_agent(
        session, code="test.agent", name="Agent", purpose="test"
    )
    agent_version = AIOrchestrationService.create_agent_version(
        session,
        agent,
        version_label="1",
        model_profile_id=model.id,
        prompt_version_id=prompt_version.id,
        tool_policy=["RULES_EVALUATE"],
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    workflow = AIOrchestrationService.create_workflow(
        session, code="test.workflow", name="Workflow", purpose="test"
    )
    workflow_version = AIOrchestrationService.create_workflow_version(
        session,
        workflow,
        version_label="1",
        graph_definition={"nodes": [{"type": "INPUT"}, {"type": "HITL"}]},
    )
    for item in (model, prompt_version, agent_version, workflow_version):
        item.status = AIArtifactStatus.PUBLISHED
    run = AIOrchestrationService.start_run(session, workflow_version, request_id="req-1")
    review = AIOrchestrationService.request_review(
        session, run, entity_type="proposal", entity_id=uuid.uuid4(), checksum="abc"
    )
    assert review.status is HumanReviewStatus.PENDING
    AIOrchestrationService.decide_review(
        session, review, decision=HumanReviewStatus.APPROVED, actor_id=uuid.uuid4(), checksum="abc"
    )
    AIOrchestrationService.resume_run(session, review, actor_id=uuid.uuid4())
    AIOrchestrationService.complete_run(session, run)
    assert run.status.value == "SUCCEEDED"
    with pytest.raises(ValueError, match="already decided"):
        AIOrchestrationService.decide_review(
            session,
            review,
            decision=HumanReviewStatus.REJECTED,
            actor_id=uuid.uuid4(),
            checksum="abc",
        )


def test_forbidden_tools_are_rejected(session):
    model = AIOrchestrationService.create_model(
        session, code="test.model.2", name="Test", provider="TEST", model_name="v1", purpose="test"
    )
    prompt = AIOrchestrationService.create_prompt(
        session, code="test.prompt.2", name="Prompt", purpose="test"
    )
    version = AIOrchestrationService.create_prompt_version(
        session, prompt, version_label="1", system_template="safe"
    )
    agent = AIOrchestrationService.create_agent(
        session, code="test.agent.2", name="Agent", purpose="test"
    )
    with pytest.raises(ValueError, match="allow-listed"):
        AIOrchestrationService.create_agent_version(
            session,
            agent,
            version_label="1",
            model_profile_id=model.id,
            prompt_version_id=version.id,
            tool_policy=["SHELL"],
            input_schema={},
            output_schema={},
        )


def test_checkpoint_is_domain_state_not_approval_and_terminal_runs_cannot_resume(session):
    model = AIOrchestrationService.create_model(
        session,
        code="test.model.checkpoint",
        name="Test",
        provider="TEST",
        model_name="v1",
        purpose="test",
    )
    prompt = AIOrchestrationService.create_prompt(
        session, code="test.prompt.checkpoint", name="Prompt", purpose="test"
    )
    prompt_version = AIOrchestrationService.create_prompt_version(
        session, prompt, version_label="1", system_template="safe"
    )
    agent = AIOrchestrationService.create_agent(
        session, code="test.agent.checkpoint", name="Agent", purpose="test"
    )
    AIOrchestrationService.create_agent_version(
        session,
        agent,
        version_label="1",
        model_profile_id=model.id,
        prompt_version_id=prompt_version.id,
        tool_policy=[],
        input_schema={},
        output_schema={},
    )
    workflow = AIOrchestrationService.create_workflow(
        session, code="test.workflow.checkpoint", name="Workflow", purpose="test"
    )
    workflow_version = AIOrchestrationService.create_workflow_version(
        session, workflow, version_label="1", graph_definition={"nodes": [{"type": "HITL"}]}
    )
    workflow_version.status = AIArtifactStatus.PUBLISHED
    run = AIOrchestrationService.start_run(session, workflow_version, request_id="checkpoint-1")
    review = AIOrchestrationService.request_review(
        session, run, entity_type="proposal", entity_id=uuid.uuid4()
    )
    checkpoint = AIOrchestrationService.save_checkpoint(
        session, run, workflow_version, node_id="HITL", state={"proposal": "synthetic"}
    )
    assert checkpoint.status == "ACTIVE"
    assert run.status.value == "WAITING_FOR_HUMAN"
    AIOrchestrationService.decide_review(
        session, review, decision=HumanReviewStatus.APPROVED, actor_id=uuid.uuid4()
    )
    AIOrchestrationService.resume_run(session, review, actor_id=uuid.uuid4())
    AIOrchestrationService.complete_run(session, run)
    with pytest.raises(ValueError, match="terminal run"):
        AIOrchestrationService.load_checkpoint(session, run)


def test_run_lifecycle_audit_events_are_safe_and_resume_is_idempotent(session):
    context = _platform_context()
    model = AIOrchestrationService.create_model(
        session,
        code="audit.model",
        name="Test",
        provider="TEST",
        model_name="v1",
        purpose="test",
        context=context,
    )
    prompt = AIOrchestrationService.create_prompt(
        session, code="audit.prompt", name="Prompt", purpose="test", context=context
    )
    prompt_version = AIOrchestrationService.create_prompt_version(
        session, prompt, version_label="1", system_template="safe", context=context
    )
    agent = AIOrchestrationService.create_agent(
        session, code="audit.agent", name="Agent", purpose="test", context=context
    )
    AIOrchestrationService.create_agent_version(
        session,
        agent,
        version_label="1",
        model_profile_id=model.id,
        prompt_version_id=prompt_version.id,
        tool_policy=[],
        input_schema={},
        output_schema={},
        context=context,
    )
    workflow = AIOrchestrationService.create_workflow(
        session, code="audit.workflow", name="Workflow", purpose="test", context=context
    )
    workflow_version = AIOrchestrationService.create_workflow_version(
        session,
        workflow,
        version_label="1",
        graph_definition={"nodes": [{"type": "HITL"}]},
        context=context,
    )
    workflow_version.status = AIArtifactStatus.PUBLISHED
    run = AIOrchestrationService.start_run(
        session, workflow_version, request_id="audit-1", context=context
    )
    review = AIOrchestrationService.request_review(
        session, run, entity_type="proposal", entity_id=uuid.uuid4(), context=context
    )
    AIOrchestrationService.decide_review(
        session,
        review,
        decision=HumanReviewStatus.APPROVED,
        actor_id=context.user_id,
        context=context,
    )
    AIOrchestrationService.resume_run(session, review, actor_id=context.user_id, context=context)
    with pytest.raises(ValueError, match="not waiting"):
        AIOrchestrationService.resume_run(
            session, review, actor_id=context.user_id, context=context
        )
    AIOrchestrationService.complete_run(session, run, context=context)
    events = {event.event_type for event in session.query(AuditEvent).all()}
    assert {
        "ai.run.created",
        "ai.workflow.run.started",
        "ai.review.requested",
        "ai.review.approved",
        "ai.workflow.resumed",
        "ai.workflow.run.completed",
    } <= events
