"""Exact draft improvement evidence on guarded, Alembic-migrated PostgreSQL."""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.ai_orchestration.service import AIOrchestrationService
from app.authorization import AuthorizationError, build_tenant_context
from app.config import Settings
from app.evaluation import regression_registry, regression_review
from app.evaluation import service as evaluation
from app.evaluation.prompt_policy import PromptPolicyBaselineInput, execute_prompt_policy_baseline
from app.evaluation.schemas import DatasetVersionInput
from app.feedback import intake, proposals
from app.feedback.common import FeedbackConflict, latest
from app.improvement import candidates, readiness
from app.models import (
    AIRun,
    AuditEvent,
    EvaluationReview,
    EvaluationRun,
    HumanReviewRequest,
    Organisation,
    OrganisationMembership,
    User,
)
from app.models.ai_orchestration import PromptVersion
from app.models.enums import AIArtifactStatus, AIRunStatus, HumanReviewStatus, OrganisationType
from app.models.feedback import FeedbackOperation, GovernedChangeCandidate
from app.writing_execution import registry as writing_registry
from tests.alembic_helpers import repository_alembic_head
from tests.test_evaluation_foundation_postgresql_integration import dataset_body
from tests.test_evaluation_regression_postgresql_integration import _promotion, _publish, _suite
from tests.test_expert_references_postgresql_integration import expert as expert
from tests.test_governed_feedback_postgresql_integration import (
    _confirm,
    _pattern,
    _proposal_decision,
    _summary_pin,
    _triage,
)

REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="Governed improvement acceptance requires isolated Alembic PostgreSQL",
)
CHECK = "prompt_channel_isolation"
RATIONALE = "Verified complete synthetic request checks for nonproduction only."


def _pin(row):
    return {"id": str(row.id), "checksum": row.checksum}


def _case():
    key = uuid.uuid4().hex
    canary = "PRUFAI_SYNTHETIC_CANARY_" + key
    identity = {"synthetic_fixture_id": "synthetic:" + canary}
    return {
        "case_id": "prompt-policy-" + key,
        "kind": "WRITING",
        "split": "TEST",
        "input": {
            "text": canary,
            "modality": "TEXT",
            "adversarial": True,
            "evidence_identity": identity,
        },
        "reference": {
            "label_kind": "SYNTHETIC_EXPECTATION",
            "criteria": [{"code": "TASK", "max_points": "4"}],
            "evidence_identity": identity,
        },
    }


def _dataset(expert, session, cases, **overrides):
    creator = expert.context(session)
    body = {**dataset_body(cases=cases), "skill": "WRITING", **overrides}
    version = evaluation.create_dataset_version(
        session, creator, DatasetVersionInput.model_validate(body)
    )
    for action, context in (
        ("submit", creator),
        ("approve", expert.context(session, "approver")),
        ("publish", creator),
    ):
        evaluation.transition_dataset(
            session, context, version.id, action, version.checksum, "synthetic:source-review"
        )
    return version


def _baseline_input(expert, prompt, dataset):
    return PromptPolicyBaselineInput.model_validate(
        {
            "operation_id": str(uuid.uuid4()),
            "prompt": _pin(prompt),
            "dataset": _pin(dataset),
            "standard_pack": {"id": str(expert.pack_id), "checksum": expert.pack_checksum},
            "split": "TEST",
        }
    )


@dataclass
class Scenario:
    payload: candidates.ChangeCandidateInput
    prompt: PromptVersion
    profile: dict
    baseline: EvaluationRun
    cases: list[dict]


def _setup(
    expert,
    *,
    retained_canary=False,
    partial_suite=False,
    hypothesis="PREVENT_RECORDED_FAILURE",
    category="ACCESS_CONTROL",
):
    cases = [_case(), _case()]
    tokens = [case["input"]["text"] for case in cases]
    settings = Settings(_env_file=None, environment="test", writing_test_adapter_enabled=True)
    with expert.factory() as session:
        creator = expert.context(session)
        examiner = expert.context(session, "examiner-b")
        approver = expert.context(session, "approver")
        profile = writing_registry.create_execution_profile(
            session,
            context=creator,
            code="synthetic-improvement-" + uuid.uuid4().hex,
            name="Synthetic technical fixture",
            provider="DETERMINISTIC_TEST",
            model_name="writing-fixture-v1",
            configuration={"schema_version": "1", "adapter_version": "1"},
            system_template="Synthetic Writing fixture system policy."
            + (" " + tokens[0] if retained_canary else ""),
            instruction_template="Legacy fixture instructions: " + " ".join(tokens),
        )
        profile = writing_registry.qualify_test_execution_profile(
            session,
            snapshot=profile,
            operator=session.get(User, creator.user_id),
            settings=settings,
        )
        prompt = session.get(PromptVersion, uuid.UUID(profile["prompt_version_id"]))
        assert prompt is not None and prompt.status == AIArtifactStatus.PUBLISHED
        dataset = _dataset(expert, session, cases)
        baseline_input = _baseline_input(expert, prompt, dataset)
        baseline = execute_prompt_policy_baseline(session, examiner, baseline_input)
        assert execute_prompt_policy_baseline(session, examiner, baseline_input).id == baseline.id
        assert baseline.status == "COMPLETED" and baseline.hard_gate == "FAIL"
        assert baseline.case_count == 2 and baseline.failure_count == 2
        assert (baseline.evidence_class, baseline.execution_class) == (
            "TECHNICAL_TEST",
            "TEST_ONLY",
        )
        results = evaluation.verify_run(session, baseline)
        feedback, promotions = [], []
        for result in results:
            receipt = intake.intake_feedback(
                session,
                creator,
                intake.FeedbackIntakeInput.model_validate(
                    {
                        "operation_id": str(uuid.uuid4()),
                        "source": {
                            "kind": "EVALUATION_FAILURE",
                            "run": _pin(baseline),
                            "result": _pin(result),
                            "failed_check": CHECK,
                        },
                        "category": category,
                        "severity": "MEDIUM",
                    }
                ),
            )
            feedback.append(_confirm(expert, session, _triage(expert, session, receipt)))
            promotions.append(
                _publish(
                    expert,
                    session,
                    regression_registry.promote_case(
                        session, creator, _promotion(baseline, result, CHECK)
                    ),
                )
            )
        pattern = _pattern(expert, session, feedback[0])
        assert pattern["member_count"] == 2
        _, suite = _suite(expert, session, promotions[:1] if partial_suite else promotions)
        target = {"type": "PROMPT_VERSION", **_pin(prompt)}
        proposal = proposals.create_proposal(
            session,
            creator,
            proposals.ProposalInput.model_validate(
                {
                    "operation_id": str(uuid.uuid4()),
                    "pattern": _summary_pin(pattern),
                    "target": target,
                    "change_category": "PROMPT_REVISION",
                    "hypothesis_code": hypothesis,
                    "suite_version": _pin(suite),
                    "baseline_run": _pin(baseline),
                }
            ),
        )
        approved = proposals.decide_proposal(
            session, approver, uuid.UUID(proposal["id"]), _proposal_decision(proposal)
        )
        assert approved["status"] == "APPROVED_FOR_EVALUATION"
        payload = candidates.ChangeCandidateInput.model_validate(
            {
                "operation_id": str(uuid.uuid4()),
                "proposal": _summary_pin(approved),
                "approval": approved["receipt"],
                "target": target,
                "suite_version": _pin(suite),
                "baseline_run": _pin(baseline),
            }
        )
        session.commit()
        return Scenario(payload, prompt, profile, baseline, cases)


def _create(expert, session, scenario):
    row = candidates.create_change_candidate(session, expert.context(session), scenario.payload)
    session.commit()
    return row


def _evaluate(expert, session, row):
    payload = readiness.EvaluateCandidateInput(
        operation_id=uuid.uuid4(),
        expected_checksum=latest(session, readiness.KIND, row.id).checksum,
    )
    receipt = readiness.evaluate_candidate(
        session, expert.context(session, "examiner-a"), row.id, payload
    )
    session.commit()
    return receipt, payload


def _approve(receipt, **changes):
    return readiness.ReviewCandidateInput.model_validate(
        {
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": receipt.checksum,
            "decision": "APPROVE_FOR_NONPRODUCTION_VALIDATION",
            "rationale": RATIONALE,
            **changes,
        }
    )


def test_measured_two_case_prompt_successor_and_independent_readiness(expert):
    scenario = _setup(expert)
    with expert.factory() as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == repository_alembic_head()
        )
        row = _create(expert, session, scenario)
        assert row.proposal_version_id == scenario.payload.proposal.id
        assert row.baseline_prompt_version_id == scenario.prompt.id
        assert row.body["approval"] == scenario.payload.approval.model_dump(mode="json")
        draft = session.get(PromptVersion, row.draft_prompt_version_id)
        assert draft is not None and draft.status == AIArtifactStatus.DRAFT
        assert draft.checksum == row.body["draft"]["checksum"] != scenario.prompt.checksum
        assert draft.prompt_definition_id == scenario.prompt.prompt_definition_id
        assert draft.system_template == scenario.prompt.system_template
        assert draft.instruction_template is None
        receipt, evaluation_payload = _evaluate(expert, session, row)
        assert receipt.state["status"] == "HUMAN_REVIEW_REQUIRED"
        assert receipt.state["production_readiness"] == "NOT_READY"
        details = readiness.candidate_detail(session, expert.context(session, "adjudicator"), row)
        regression = details["comparison"]["regression"]
        assert regression["paired_case_count"] == regression["expected_case_count"] == 2
        assert regression["baseline_run"] == _pin(scenario.baseline)
        assert regression["candidate_artifacts"]["prompt_version_id"] == str(draft.id)
        assert regression["candidate_artifacts"]["prompt_checksum"] == draft.checksum
        assert regression["empirical_status"] == "UNCONFIGURED"
        assert regression["execution_class"] == "TEST_ONLY"
        for case in regression["case_findings"]:
            check = next(check for check in case["checks"] if check["code"] == CHECK)
            assert (
                check["baseline"]["status"],
                check["baseline"]["numerator"],
                check["baseline"]["denominator"],
            ) == ("FAIL", 0, 1)
            assert (
                check["candidate"]["status"],
                check["candidate"]["numerator"],
                check["candidate"]["denominator"],
            ) == ("PASS", 1, 1)
        for diff in details["evaluation"]["case_differences"]:
            assert CHECK in diff["resolved_failures"]
            assert not diff["new_failures"] and not diff["unchanged_failures"]

        reviewer = expert.context(session, "adjudicator")
        comparison_pin = details["evaluation"]["comparison_run"]
        candidate_pin = details["evaluation"]["candidate_run"]
        with pytest.raises(
            evaluation.EvaluationConflict, match="GOVERNED_IMPROVEMENT_REVIEW_REQUIRED"
        ):
            evaluation.review_run(
                session,
                reviewer,
                uuid.UUID(candidate_pin["id"]),
                candidate_pin["checksum"],
                "ACCEPTED",
                "synthetic:unsafe-review",
            )
        with pytest.raises(
            evaluation.EvaluationConflict, match="GOVERNED_IMPROVEMENT_REVIEW_REQUIRED"
        ):
            regression_review.record_review(
                session,
                reviewer,
                uuid.UUID(comparison_pin["id"]),
                regression_review.ComparisonReviewInput(
                    operation_id=uuid.uuid4(),
                    expected_checksum=comparison_pin["checksum"],
                    decision="ACCEPTED",
                    review_reference="synthetic:unsafe-review",
                ),
            )
        for governed_id in (row.id, draft.id):
            with pytest.raises(ValueError, match="domain governed decision"):
                AIOrchestrationService.request_review(
                    session,
                    AIRun(id=uuid.uuid4(), status=AIRunStatus.RUNNING),
                    entity_type="GENERIC",
                    entity_id=governed_id,
                )
            with pytest.raises(ValueError, match="domain governed decision"):
                AIOrchestrationService.decide_review(
                    session,
                    HumanReviewRequest(
                        review_type="AI_PROPOSAL",
                        entity_type="GENERIC",
                        entity_id=governed_id,
                        status=HumanReviewStatus.PENDING,
                    ),
                    decision=HumanReviewStatus.APPROVED,
                    actor_id=reviewer.user_id,
                )

        command = _approve(receipt)
        for actor in ("creator", "approver", "examiner-a", "examiner-b", "candidate"):
            with pytest.raises(AuthorizationError):
                readiness.review_candidate(session, expert.context(session, actor), row.id, command)
            session.rollback()
        approved = readiness.review_candidate(
            session, expert.context(session, "adjudicator"), row.id, command
        )
        session.commit()
        assert approved.state["status"] == "APPROVED_FOR_NONPRODUCTION_VALIDATION"
        assert approved.state["production_readiness"] == "NOT_READY"
        review = session.get(EvaluationReview, uuid.UUID(approved.state["evaluation_review_id"]))
        assert review is not None and review.reviewer_user_id == expert.actors["adjudicator"]
        assert review.run_checksum == details["evaluation"]["comparison_run"]["checksum"]
        assert (
            readiness.evaluate_candidate(
                session, expert.context(session, "examiner-a"), row.id, evaluation_payload
            ).id
            == receipt.id
        )
        assert (
            readiness.review_candidate(
                session, expert.context(session, "adjudicator"), row.id, command
            ).id
            == approved.id
        )
        with pytest.raises(FeedbackConflict, match="OPERATION_CONFLICT"):
            readiness.review_candidate(
                session,
                expert.context(session, "adjudicator"),
                row.id,
                command.model_copy(update={"rationale": "Changed request after review."}),
            )
        session.rollback()

        observer = readiness.candidate_detail(session, expert.context(session, "observer"), row)
        assert observer["allowed_actions"] == []
        assert observer["production_readiness"] == "NOT_READY"
        assert observer["comparison"]["release_recommendation"] == "NOT_READY"
        assert observer["comparison"]["production_activation"] is False
        assert "rationale" not in observer["history"][-1]
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.entity_id == str(row.id)))
        )
        assert {event.event_type for event in events} == {
            "feedback.change_candidate_created",
            "feedback.change_candidate_evaluated",
            "feedback.change_candidate_reviewed",
        }
        assert {event.request_id for event in events} == {
            str(scenario.payload.operation_id),
            str(evaluation_payload.operation_id),
            str(command.operation_id),
        }
        assert approved.previous_checksum == receipt.checksum
        exported = json.dumps([observer, *[event.metadata_json for event in events]])
        assert "PRUFAI_SYNTHETIC_CANARY_" not in exported and RATIONALE not in exported
        assert "Legacy fixture instructions" not in exported
        assert session.get(PromptVersion, scenario.prompt.id).checksum == scenario.prompt.checksum
        assert session.get(PromptVersion, scenario.prompt.id).status == AIArtifactStatus.PUBLISHED
        assert session.get(PromptVersion, draft.id).status == AIArtifactStatus.DRAFT

        for statement, identifier, code in (
            (
                "UPDATE prompt_versions SET status = 'PUBLISHED' WHERE id = :id",
                draft.id,
                "GOVERNED_IMPROVEMENT_DRAFT_IMMUTABLE",
            ),
            (
                "UPDATE governed_change_candidates SET checksum = repeat('0',64) WHERE id = :id",
                row.id,
                "FEEDBACK_HISTORY_IMMUTABLE",
            ),
            (
                "DELETE FROM feedback_operations WHERE id = :id",
                approved.id,
                "FEEDBACK_HISTORY_IMMUTABLE",
            ),
            (
                "UPDATE evaluation_runs SET failure_count = 99 WHERE id = :id",
                review.run_id,
                "IMMUTABLE",
            ),
            ("DELETE FROM evaluation_reviews WHERE id = :id", review.id, "IMMUTABLE"),
        ):
            with pytest.raises(DBAPIError, match=code):
                session.execute(text(statement), {"id": identifier})
            session.rollback()


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"retained_canary": True}, "TECHNICAL_GATE_FAILED"),
        ({"partial_suite": True}, "NOT_COMPARABLE"),
    ],
)
def test_failed_or_incompatible_comparison_cannot_get_nonproduction_approval(
    expert, options, expected
):
    scenario = _setup(expert, **options)
    with expert.factory() as session:
        row = _create(expert, session, scenario)
        receipt, _ = _evaluate(expert, session, row)
        assert receipt.state["status"] == expected
        reviewer = expert.context(session, "adjudicator")
        with pytest.raises(FeedbackConflict, match="NONPRODUCTION_VALIDATION_NOT_ELIGIBLE"):
            readiness.review_candidate(session, reviewer, row.id, _approve(receipt))
        session.rollback()
        recorded = readiness.review_candidate(
            session, reviewer, row.id, _approve(receipt, decision="NOT_READY")
        )
        session.commit()
        assert recorded.state["status"] == recorded.state["production_readiness"] == "NOT_READY"
        assert recorded.state["evaluation"] == receipt.state["evaluation"]


def test_held_out_tenant_scope_stale_target_and_closed_approval_are_denied(expert):
    scenario = _setup(expert)
    with expert.factory() as session:
        creator = expert.context(session)
        examiner = expert.context(session, "examiner-b")
        organisations = [
            Organisation(
                name="Synthetic isolation test school",
                slug="synthetic-isolation-" + uuid.uuid4().hex,
                organisation_type=OrganisationType.LANGUAGE_SCHOOL,
            )
            for _ in range(2)
        ]
        session.add_all(organisations)
        session.flush()
        for user_id, organisation in zip(
            (creator.user_id, expert.actors["candidate"]), organisations, strict=True
        ):
            session.add(OrganisationMembership(organisation_id=organisation.id, user_id=user_id))
        session.flush()
        tenant_context = build_tenant_context(session, creator.user_id, organisations[0].id)
        foreign_context = build_tenant_context(
            session, expert.actors["candidate"], organisations[1].id
        )
        assert tenant_context.membership_id and foreign_context.membership_id
        assert tenant_context.organisation_id != foreign_context.organisation_id
        held = _case()
        held["split"] = "HELD_OUT"
        held_dataset = _dataset(expert, session, [held])
        private_dataset = _dataset(
            expert, session, [_case()], organisation_id=str(organisations[1].id)
        )
        session.commit()
        for dataset, error in (
            (held_dataset, "HELD_OUT_UNAVAILABLE"),
            (private_dataset, "PUBLIC_SYNTHETIC_DATASET_REQUIRED"),
        ):
            with pytest.raises(AuthorizationError, match=error):
                execute_prompt_policy_baseline(
                    session, examiner, _baseline_input(expert, scenario.prompt, dataset)
                )
            session.rollback()
        with pytest.raises(FeedbackConflict):
            candidates.create_change_candidate(
                session,
                creator,
                scenario.payload.model_copy(
                    update={
                        "target": scenario.payload.target.model_copy(update={"checksum": "0" * 64})
                    }
                ),
            )
        session.rollback()
        row = _create(expert, session, scenario)
        with pytest.raises(AuthorizationError):
            candidates.create_change_candidate(session, tenant_context, scenario.payload)
        session.rollback()
        with pytest.raises(AuthorizationError):
            readiness.candidate_detail(session, tenant_context, row)
        session.rollback()
        with pytest.raises(AuthorizationError):
            candidates.create_change_candidate(session, foreign_context, scenario.payload)
        session.rollback()
        with pytest.raises(AuthorizationError):
            readiness.candidate_detail(session, foreign_context, row)
        session.rollback()
        proposals.decide_proposal(
            session,
            expert.context(session, "approver"),
            row.proposal_version_id,
            proposals.ProposalDecisionInput(
                operation_id=uuid.uuid4(),
                expected_checksum=scenario.payload.approval.checksum,
                action="CLOSE",
                reason_code="WITHDRAWN",
            ),
        )
        session.commit()
        detail = readiness.candidate_detail(session, creator, row)
        assert detail["status"] == "AWAITING_EVALUATION"
        assert detail["proposal_authority_current"] is False and detail["allowed_actions"] == []
        with pytest.raises(FeedbackConflict, match="APPROVAL_STALE"):
            _evaluate(expert, session, row)
        session.rollback()


def _race(expert, invoke):
    barrier = Barrier(2)

    def run(index):
        with expert.factory() as session:
            barrier.wait(timeout=10)
            try:
                result = invoke(session, index)
                session.commit()
                return result.id
            except FeedbackConflict:
                session.rollback()
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run, range(2)))


def test_postgresql_serializes_candidate_submissions_evaluations_and_readiness(expert):
    scenario = _setup(expert)
    payloads = [
        scenario.payload.model_copy(update={"operation_id": uuid.uuid4()}) for _ in range(2)
    ]
    created = _race(
        expert,
        lambda session, index: candidates.create_change_candidate(
            session, expert.context(session), payloads[index]
        ),
    )
    winners = set(created) - {None}
    assert len(winners) == 1
    identifier = winners.pop()
    with expert.factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(GovernedChangeCandidate)
                .where(GovernedChangeCandidate.proposal_version_id == scenario.payload.proposal.id)
            )
            == 1
        )
        initial_checksum = latest(session, readiness.KIND, identifier).checksum
        exact = payloads[created.index(identifier)]
        assert (
            candidates.create_change_candidate(session, expert.context(session), exact).id
            == identifier
        )
        with pytest.raises(FeedbackConflict, match="OPERATION_CONFLICT"):
            candidates.create_change_candidate(
                session,
                expert.context(session),
                exact.model_copy(
                    update={
                        "baseline_run": exact.baseline_run.model_copy(update={"checksum": "0" * 64})
                    }
                ),
            )
        session.rollback()

    evaluated = _race(
        expert,
        lambda session, _: readiness.evaluate_candidate(
            session,
            expert.context(session, "examiner-a"),
            identifier,
            readiness.EvaluateCandidateInput(
                operation_id=uuid.uuid4(), expected_checksum=initial_checksum
            ),
        ),
    )
    assert evaluated.count(None) == 1
    with expert.factory() as session:
        receipt = latest(session, readiness.KIND, identifier)
        assert receipt.id in evaluated and receipt.revision == 2
    reviewed = _race(
        expert,
        lambda session, _: readiness.review_candidate(
            session, expert.context(session, "adjudicator"), identifier, _approve(receipt)
        ),
    )
    assert reviewed.count(None) == 1
    with expert.factory() as session:
        current = latest(session, readiness.KIND, identifier)
        assert current.id in reviewed and current.revision == 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(FeedbackOperation)
                .where(
                    FeedbackOperation.entity_kind == readiness.KIND,
                    FeedbackOperation.entity_id == identifier,
                )
            )
            == 3
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(EvaluationReview)
                .where(
                    EvaluationReview.run_id
                    == uuid.UUID(receipt.state["evaluation"]["comparison_run"]["id"])
                )
            )
            == 1
        )


def test_existing_execution_profile_qualification_rejects_sealed_draft(expert):
    from app.ai_orchestration.service import AIOrchestrationService
    from app.models import AgentVersion, AIWorkflowVersion

    scenario = _setup(expert)
    with expert.factory() as session:
        candidate = _create(expert, session, scenario)
        draft = session.get(PromptVersion, candidate.draft_prompt_version_id)
        creator = expert.context(session)
        operator = session.get(User, creator.user_id)
        baseline_agent = session.get(AgentVersion, uuid.UUID(scenario.profile["agent_version_id"]))
        workflow = session.get(
            AIWorkflowVersion, uuid.UUID(scenario.profile["workflow_version_id"])
        )
        assert draft is not None and operator is not None and baseline_agent is not None
        assert workflow is not None
        agent = AIOrchestrationService.create_agent_version(
            session,
            baseline_agent.definition,
            version_label="synthetic-qualification-guard-probe",
            model_profile_id=baseline_agent.model_profile_id,
            prompt_version_id=draft.id,
            tool_policy=baseline_agent.tool_policy,
            input_schema=baseline_agent.input_schema,
            output_schema=baseline_agent.output_schema,
            context=creator,
        )
        snapshot = writing_registry.snapshot_execution_profile(
            session,
            model_profile_id=agent.model_profile_id,
            prompt_version_id=draft.id,
            agent_version_id=agent.id,
            workflow_version_id=workflow.id,
        )
        assert snapshot["prompt_checksum"] == draft.checksum
        assert writing_registry.approved_profile_record(session, snapshot) is None
        with pytest.raises(ValueError, match="GOVERNED_CHANGE_CANDIDATE_CANNOT_BE_QUALIFIED"):
            writing_registry.qualify_test_execution_profile(
                session,
                snapshot=snapshot,
                operator=operator,
                settings=Settings(
                    _env_file=None, environment="test", writing_test_adapter_enabled=True
                ),
            )
        assert draft.status == agent.status == AIArtifactStatus.DRAFT
        assert workflow.status == scenario.prompt.status == AIArtifactStatus.PUBLISHED
        assert draft.approved_at is None and draft.published_at is None
        assert writing_registry.approved_profile_record(session, snapshot) is None


@pytest.mark.parametrize("transition", ["evaluate", "review"])
def test_retired_baseline_blocks_new_transitions_but_preserves_history(expert, transition):
    scenario = _setup(expert)
    with expert.factory() as session:
        row = _create(expert, session, scenario)
        receipt = None
        if transition == "review":
            receipt, _ = _evaluate(expert, session, row)
        baseline = session.get(PromptVersion, scenario.prompt.id)
        assert baseline is not None and baseline.checksum == scenario.prompt.checksum
        baseline.status = AIArtifactStatus.DEPRECATED
        session.commit()
        reviewer = expert.context(session, "adjudicator")
        before = readiness.candidate_detail(session, reviewer, row)
        assert before["proposal_authority_current"] is False and before["allowed_actions"] == []
        with pytest.raises(FeedbackConflict, match="TARGET_VERSION_MISMATCH"):
            if receipt is None:
                _evaluate(expert, session, row)
            else:
                readiness.review_candidate(session, reviewer, row.id, _approve(receipt))
        session.rollback()
        after = readiness.candidate_detail(session, reviewer, row)
        assert before["receipt"] == after["receipt"] and before["history"] == after["history"]


@pytest.mark.parametrize(
    ("hypothesis", "category", "reason"),
    [
        ("IMPROVE_REFERENCE_AGREEMENT", "ACCESS_CONTROL", "HYPOTHESIS_UNSUPPORTED"),
        ("PREVENT_RECORDED_FAILURE", "SCORING_CORRECTNESS", "APPROVED_REFERENCE_REQUIRED"),
    ],
)
def test_proposal_requiring_expert_evidence_cannot_use_offline_lane(
    expert, hypothesis, category, reason
):
    scenario = _setup(expert, hypothesis=hypothesis, category=category)
    with expert.factory() as session:
        creator = expert.context(session)
        with pytest.raises(FeedbackConflict, match=reason):
            candidates.create_change_candidate(session, creator, scenario.payload)
        session.rollback()
        assert (
            session.scalar(
                select(GovernedChangeCandidate.id).where(
                    GovernedChangeCandidate.proposal_version_id == scenario.payload.proposal.id
                )
            )
            is None
        )
