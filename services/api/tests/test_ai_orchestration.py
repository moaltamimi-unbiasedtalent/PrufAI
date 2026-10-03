from __future__ import annotations

import uuid

import pytest

from app.ai_orchestration.service import AIOrchestrationService
from app.models.enums import AIArtifactStatus, HumanReviewStatus


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
