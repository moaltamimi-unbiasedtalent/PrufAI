"""Promotion acceptance on isolated PostgreSQL with the actual Alembic schema."""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.authorization import AuthorizationError, build_tenant_context
from app.config import Settings
from app.feedback import proposals
from app.feedback.common import FeedbackConflict, Pin
from app.improvement import candidates, promotion, readiness, validation
from app.improvement.boundary import NONPRODUCTION, require_validation_environment
from app.models import (
    AIRun,
    AuditEvent,
    MembershipRole,
    Organisation,
    OrganisationMembership,
    PlatformRoleAssignment,
    ResultVersion,
    Role,
    SubjectiveAssessmentVersion,
    User,
    WritingExecutionJob,
)
from app.models.ai_orchestration import PromptVersion
from app.models.enums import (
    AIArtifactStatus,
    OrganisationType,
    PlatformAssignmentStatus,
    UserStatus,
)
from app.models.feedback import FeedbackOperation, ImprovementProposal
from app.writing_execution import registry as writing_registry
from tests.alembic_helpers import repository_alembic_head
from tests.test_expert_references_postgresql_integration import expert as expert
from tests.test_governed_improvement_postgresql_integration import (
    _approve,
    _create,
    _evaluate,
    _pin,
    _setup,
)

REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="Prompt promotion acceptance requires isolated Alembic PostgreSQL",
)
RATIONALE = "Review confirmed the technical scope and nonproduction-only controls."


def _settings(expert, **changes):
    return Settings(
        _env_file=None,
        **{
            "environment": "test",
            "database_url": expert.database_url,
            "nonproduction_prompt_validation_enabled": True,
            "nonproduction_prompt_validation_database": make_url(expert.database_url).database,
            "writing_test_adapter_enabled": True,
            **changes,
        },
    )


def _ready(expert, session, row):
    evaluated, _ = _evaluate(expert, session, row)
    decision = readiness.review_candidate(
        session, expert.context(session, "adjudicator"), row.id, _approve(evaluated)
    )
    session.commit()
    return decision


def _select_input(decision, previous=None, **changes):
    return promotion.PromotionInput.model_validate(
        {
            "operation_id": str(uuid.uuid4()),
            "environment": NONPRODUCTION,
            "readiness": _pin(decision) if decision is not None else None,
            "expected_selection": _pin(previous) if previous else None,
            "rationale": RATIONALE,
            **changes,
        }
    )


def _validate_input(receipt, **changes):
    return validation.ValidationInput.model_validate(
        {
            "operation_id": str(uuid.uuid4()),
            "environment": NONPRODUCTION,
            "selection": _pin(receipt),
            **changes,
        }
    )


def _revert_input(selection):
    return promotion.RevertInput.model_validate(
        {
            "operation_id": str(uuid.uuid4()),
            "environment": NONPRODUCTION,
            "expected_selection": _pin(selection),
            "rationale": "Return the isolated environment to the previous permitted selection.",
        }
    )


def _selection_count(session, definition_id):
    return session.scalar(
        select(func.count())
        .select_from(FeedbackOperation)
        .where(
            FeedbackOperation.entity_kind == "PROMPT_SELECTION",
            FeedbackOperation.entity_id == definition_id,
        )
    )


def _counts(session):
    return {
        model.__name__: session.scalar(select(func.count()).select_from(model))
        for model in (AIRun, WritingExecutionJob, SubjectiveAssessmentVersion, ResultVersion)
    }


def test_real_isolated_writing_validation_and_immutable_reversion(expert):
    scenario = _setup(expert)
    settings = _settings(expert)
    with expert.factory() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == (
            repository_alembic_head()
        )
        row = _create(expert, session, scenario)
        decision = _ready(expert, session, row)
        authorizer = expert.context(session, "revoked")
        evaluator = expert.context(session, "examiner-a")
        draft = session.get(PromptVersion, row.draft_prompt_version_id)
        baseline = session.get(PromptVersion, row.baseline_prompt_version_id)
        assert draft is not None and baseline is not None
        assert baseline.status == AIArtifactStatus.PUBLISHED
        assert writing_registry.snapshot_registry_valid(session, scenario.profile)
        start = _counts(session)

        command = _select_input(decision)
        selection = promotion.promote(session, authorizer, settings, row.id, command)
        session.commit()
        assert selection.state["status"] == "SELECTED"
        assert selection.state["environment"] == NONPRODUCTION
        assert selection.state["selected"]["prompt"] == _pin(draft)
        assert selection.state["selected"]["baseline"] == _pin(baseline)
        assert selection.state["selected"]["proposal"] == row.body["proposal"]
        assert selection.state["selected"]["readiness"] == _pin(decision)
        for key in ("baseline_run", "candidate_run", "comparison_run"):
            assert selection.state["selected"][key] == decision.state["evaluation"][key]
        assert promotion.promote(session, authorizer, settings, row.id, command).id == selection.id
        with pytest.raises(FeedbackConflict, match="OPERATION_CONFLICT"):
            promotion.promote(
                session,
                authorizer,
                settings,
                row.id,
                command.model_copy(update={"rationale": "Altered input for a repeated operation."}),
            )
        session.rollback()
        repeated_selection = promotion.promote(
            session, authorizer, settings, row.id, _select_input(decision, selection)
        )
        session.commit()
        assert repeated_selection.state["status"] == "DENIED"
        assert promotion.selection_summary(repeated_selection)["reason_code"] == (
            "PROMOTION_ALREADY_SELECTED"
        )
        assert _selection_count(session, draft.prompt_definition_id) == 1

        validate = _validate_input(selection)
        executed = validation.validate_selection(session, evaluator, settings, row.id, validate)
        session.commit()
        proof = validation.validation_summary(executed)
        assert proof["status"] == "COMPLETED"
        assert proof["environment"] == NONPRODUCTION and proof["execution_class"] == "TEST_ONLY"
        assert proof["model_execution"] == "NOT_RUN" and proof["production_activation"] is False
        assert proof["prompt"] == _pin(draft) and proof["selection"] == _pin(selection)
        assert proof["case_count"] == len(proof["cases"]) == 2
        for case in proof["cases"]:
            assert case["validated_criteria_count"] == 1
            for key in ("input_checksum", "request_checksum", "output_checksum"):
                assert len(case[key]) == 64
        assert validation.validate_selection(session, evaluator, settings, row.id, validate).id == (
            executed.id
        )
        assert _counts(session) == start
        assert writing_registry.snapshot_registry_valid(session, scenario.profile)
        assert not writing_registry.snapshot_registry_valid(
            session, {**scenario.profile, "prompt_version_id": str(draft.id)}
        )
        assert draft.status == AIArtifactStatus.DRAFT
        assert baseline.status == AIArtifactStatus.PUBLISHED
        assert baseline.checksum == scenario.prompt.checksum

        denied = promotion.promote(
            session,
            authorizer,
            settings,
            row.id,
            _select_input(decision, selection, environment="PRODUCTION"),
        )
        session.commit()
        assert denied.state["status"] == "DENIED"
        assert promotion.selection_summary(denied)["reason_code"] == "EMPIRICAL_GATE_UNCONFIGURED"
        assert _selection_count(session, draft.prompt_definition_id) == 1
        with pytest.raises(AuthorizationError):
            validation.validate_selection(
                session,
                evaluator,
                settings,
                row.id,
                _validate_input(selection, environment="PRODUCTION"),
            )
        session.rollback()

        revert = _revert_input(selection)
        restored = promotion.revert(session, authorizer, settings, row.id, revert)
        session.commit()
        assert restored.state["status"] == "REVERTED"
        assert restored.state["selected"] is None
        assert restored.previous_checksum == selection.checksum
        assert promotion.revert(session, authorizer, settings, row.id, revert).id == restored.id
        with pytest.raises((FeedbackConflict, AuthorizationError)):
            validation.validate_selection(
                session, evaluator, settings, row.id, _validate_input(selection)
            )
        session.rollback()
        view = promotion.promotion_detail(
            session, expert.context(session, "observer"), settings, row.id
        )
        assert view["production_readiness"] == "NOT_READY"
        assert view["current_selection"]["selected"] is None
        assert {event["status"] for event in view["history"]} == {"SELECTED", "REVERTED"}
        assert RATIONALE not in json.dumps(view)
        assert _counts(session) == start

        audits = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.request_id.in_(
                        [str(value.id) for value in (selection, executed, denied, restored)]
                    ),
                    AuditEvent.event_type.startswith("feedback.prompt_"),
                )
            )
        )
        assert {event.event_type for event in audits} >= {
            "feedback.prompt_promotion_requested",
            "feedback.prompt_promotion_eligibility_evaluated",
            "feedback.prompt_promotion_authorized",
            "feedback.prompt_nonproduction_selected",
            "feedback.prompt_validation_completed",
            "feedback.prompt_production_denied",
            "feedback.prompt_selection_reverted",
        }
        assert {item.request_id for item in audits} == {
            str(value.id) for value in (selection, executed, denied, restored)
        }
        serial = json.dumps([view, proof, *[event.metadata_json for event in audits]])
        assert "PRUFAI_SYNTHETIC_CANARY_" not in serial and RATIONALE not in serial
        assert "Legacy fixture instructions" not in serial
        for statement, identifier in (
            ("DELETE FROM feedback_operations WHERE id = :id", restored.id),
            ("UPDATE feedback_operations SET state = '{}' WHERE id = :id", executed.id),
            ("UPDATE audit_events SET reason = 'altered' WHERE id = :id", audits[0].id),
            ("UPDATE prompt_versions SET status = 'PUBLISHED' WHERE id = :id", draft.id),
        ):
            with pytest.raises(DBAPIError, match="IMMUTABLE"):
                session.execute(text(statement), {"id": identifier})
            session.rollback()


def test_authorizer_independent_permissions_and_revocation_at_worker_resolution(expert):
    scenario = _setup(expert)
    settings = _settings(expert)
    with expert.factory() as session:
        row = _create(expert, session, scenario)
        decision = _ready(expert, session, row)
        command = _select_input(decision)
        for actor in ("creator", "approver", "examiner-a", "examiner-b", "adjudicator"):
            denied = promotion.promote(
                session,
                expert.context(session, actor),
                settings,
                row.id,
                command.model_copy(update={"operation_id": uuid.uuid4()}),
            )
            assert denied.state["status"] == "DENIED"
            assert promotion.selection_summary(denied)["reason_code"] == (
                "PROMOTION_INDEPENDENT_AUTHORITY_REQUIRED"
            )
            session.commit()
        authorizer = expert.context(session, "revoked")
        selection = promotion.promote(session, authorizer, settings, row.id, command)
        session.commit()
        assignments = list(
            session.scalars(
                select(PlatformRoleAssignment).where(
                    PlatformRoleAssignment.user_id == authorizer.user_id
                )
            )
        )
        assert assignments
        try:
            for assignment in assignments:
                assignment.status = PlatformAssignmentStatus.SUSPENDED
            session.commit()
            with pytest.raises((AuthorizationError, FeedbackConflict)):
                validation.validate_selection(
                    session,
                    expert.context(session, "examiner-a"),
                    settings,
                    row.id,
                    _validate_input(selection),
                )
            session.rollback()
            # Cached request context cannot preserve a role that has been removed.
            with pytest.raises(AuthorizationError):
                promotion.revert(session, authorizer, settings, row.id, _revert_input(selection))
            session.rollback()
        finally:
            for assignment in assignments:
                assignment.status = PlatformAssignmentStatus.ACTIVE
            session.commit()


@pytest.mark.parametrize(
    "condition", ["missing_review", "hard_fail", "incompatible", "stale_baseline"]
)
def test_quality_and_stale_evidence_fail_closed(expert, condition):
    scenario = _setup(
        expert, retained_canary=condition == "hard_fail", partial_suite=condition == "incompatible"
    )
    settings = _settings(expert)
    with expert.factory() as session:
        row = _create(expert, session, scenario)
        if condition == "stale_baseline":
            receipt = _ready(expert, session, row)
            baseline = session.get(PromptVersion, row.baseline_prompt_version_id)
            assert baseline is not None
            baseline.status = AIArtifactStatus.DEPRECATED
            session.commit()
        elif condition == "missing_review":
            receipt = None
        else:
            receipt, _ = _evaluate(expert, session, row)
            assert receipt.state["status"] in {"TECHNICAL_GATE_FAILED", "NOT_COMPARABLE"}
        command = _select_input(
            receipt,
            **(
                {"readiness": {"id": str(uuid.uuid4()), "checksum": "0" * 64}}
                if receipt is None
                else {}
            ),
        )
        denied = promotion.promote(
            session, expert.context(session, "revoked"), settings, row.id, command
        )
        session.commit()
        assert denied.state["status"] == "DENIED"
        assert _selection_count(session, scenario.prompt.prompt_definition_id) == 0


def test_tenant_scope_and_actual_runtime_environment_cannot_be_overridden(expert):
    scenario = _setup(expert)
    settings = _settings(expert)
    with expert.factory() as session:
        row = _create(expert, session, scenario)
        decision = _ready(expert, session, row)
        actor = expert.context(session, "revoked")
        command = _select_input(decision)
        actual_production = settings.model_copy(update={"environment": "production"})
        wrong_binding = settings.model_copy(
            update={"nonproduction_prompt_validation_database": "not-the-disposable-database"}
        )
        for invalid in (actual_production, wrong_binding):
            with pytest.raises(AuthorizationError):
                require_validation_environment(session, invalid)
            denied = promotion.promote(
                session,
                actor,
                invalid,
                row.id,
                command.model_copy(update={"operation_id": uuid.uuid4()}),
            )
            assert denied.state["status"] == "DENIED"
            session.commit()
        with pytest.raises(ValueError):
            promotion.PromotionInput.model_validate(
                {
                    **command.model_dump(mode="json"),
                    "environment": "STAGING",
                }
            )
        selection = promotion.promote(session, actor, settings, row.id, command)
        session.commit()
        with pytest.raises(AuthorizationError):
            validation.validate_selection(
                session,
                expert.context(session, "examiner-a"),
                actual_production,
                row.id,
                _validate_input(selection),
            )
        session.rollback()

        reviewer_role = session.scalar(select(Role).where(Role.key == "REVIEWER"))
        assert reviewer_role is not None
        own, foreign = [
            Organisation(
                name="Synthetic promotion scope test",
                slug="synthetic-promotion-" + uuid.uuid4().hex,
                organisation_type=OrganisationType.LANGUAGE_SCHOOL,
            )
            for _ in range(2)
        ]
        session.add_all((own, foreign))
        session.flush()
        memberships = [
            OrganisationMembership(organisation_id=own.id, user_id=actor.user_id),
            OrganisationMembership(organisation_id=foreign.id, user_id=expert.actors["candidate"]),
            OrganisationMembership(organisation_id=foreign.id, user_id=expert.actors["observer"]),
        ]
        session.add_all(memberships)
        session.flush()
        session.add(MembershipRole(membership_id=memberships[2].id, role_id=reviewer_role.id))
        session.commit()
        scoped = [
            build_tenant_context(session, actor.user_id, own.id),
            build_tenant_context(session, expert.actors["candidate"], foreign.id),
            build_tenant_context(session, expert.actors["observer"], foreign.id),
        ]
        for context in scoped:
            assert context.membership_id
            with pytest.raises(AuthorizationError):
                promotion.promote(session, context, settings, row.id, command)
            session.rollback()
            with pytest.raises(AuthorizationError):
                validation.validate_selection(
                    session, context, settings, row.id, _validate_input(selection)
                )
            session.rollback()
        assert _selection_count(session, scenario.prompt.prompt_definition_id) == 1


def _second_candidate(expert, session, scenario):
    first = session.get(ImprovementProposal, scenario.payload.proposal.id)
    assert first is not None
    creator = expert.context(session)
    proposal = proposals.create_proposal(
        session,
        creator,
        proposals.ProposalInput.model_validate(
            {
                "operation_id": str(uuid.uuid4()),
                "pattern": first.body["pattern"],
                "target": scenario.payload.target.model_dump(mode="json"),
                "change_category": "VALIDATION_GUARD",
                "hypothesis_code": first.body["hypothesis_code"],
                "suite_version": scenario.payload.suite_version.model_dump(mode="json"),
                "baseline_run": scenario.payload.baseline_run.model_dump(mode="json"),
            }
        ),
    )
    approved = proposals.decide_proposal(
        session,
        expert.context(session, "approver"),
        uuid.UUID(proposal["id"]),
        proposals.ProposalDecisionInput(
            operation_id=uuid.uuid4(),
            expected_checksum=proposal["receipt"]["checksum"],
            action="APPROVE_FOR_EVALUATION",
            reason_code="EVALUATION_PLAN_SUFFICIENT",
        ),
    )
    candidate = candidates.create_change_candidate(
        session,
        creator,
        scenario.payload.model_copy(
            update={
                "operation_id": uuid.uuid4(),
                "proposal": Pin(id=approved["id"], checksum=approved["checksum"]),
                "approval": Pin.model_validate(approved["receipt"]),
            }
        ),
    )
    session.commit()
    return candidate


def test_competing_real_selections_serialize_and_restore_previous_draft(expert):
    scenario = _setup(expert)
    settings = _settings(expert)
    with expert.factory() as session:
        first = _create(expert, session, scenario)
        second = _second_candidate(expert, session, scenario)
        rows = (first, second)
        identifiers = tuple(row.id for row in rows)
        decisions = tuple(_ready(expert, session, row) for row in rows)
        assert first.draft_prompt_version_id != second.draft_prompt_version_id
    barrier = Barrier(2)

    def attempt(index):
        with expert.factory() as session:
            actor = expert.context(session, "revoked")
            command = _select_input(decisions[index])
            barrier.wait(timeout=10)
            receipt = promotion.promote(session, actor, settings, identifiers[index], command)
            session.commit()
            return receipt

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(attempt, range(2)))
    assert sorted(receipt.state["status"] for receipt in responses) == ["DENIED", "SELECTED"]
    winner = next(
        index for index, receipt in enumerate(responses) if receipt.state["status"] == "SELECTED"
    )
    loser = 1 - winner
    selected = responses[winner]
    assert (
        promotion.selection_summary(responses[loser])["reason_code"] == "PROMOTION_SELECTION_STALE"
    )
    with expert.factory() as session:
        authorizer = expert.context(session, "revoked")
        definition_id = scenario.prompt.prompt_definition_id
        assert _selection_count(session, definition_id) == 1
        changed = promotion.promote(
            session,
            authorizer,
            settings,
            identifiers[loser],
            _select_input(decisions[loser], selected),
        )
        session.commit()
        assert (
            changed.state["status"] == "SELECTED" and changed.previous_checksum == selected.checksum
        )
        restored = promotion.revert(
            session, authorizer, settings, identifiers[loser], _revert_input(changed)
        )
        session.commit()
        assert restored.state["status"] == "REVERTED"
        assert restored.state["selected"] == selected.state["selected"]
        assert restored.previous_checksum == changed.checksum
        assert _selection_count(session, definition_id) == 3
        run = validation.validate_selection(
            session,
            expert.context(session, "examiner-a"),
            settings,
            identifiers[winner],
            _validate_input(restored),
        )
        session.commit()
        assert validation.validation_summary(run)["prompt"]["id"] == str(
            rows[winner].draft_prompt_version_id
        )


def test_explicit_clear_withdraws_without_restoring_revoked_prior_selection(expert):
    scenario = _setup(expert)
    settings = _settings(expert)
    with expert.factory() as session:
        first = _create(expert, session, scenario)
        second = _second_candidate(expert, session, scenario)
        decision_a, decision_b = (_ready(expert, session, row) for row in (first, second))
        original = expert.context(session, "revoked")
        assignment = session.scalar(
            select(PlatformRoleAssignment).where(PlatformRoleAssignment.user_id == original.user_id)
        )
        assert assignment is not None
        email = "synthetic-alternate-release-" + uuid.uuid4().hex + "@prufai.invalid"
        alternate = User(email=email, normalized_email=email, status=UserStatus.ACTIVE)
        session.add(alternate)
        session.flush()
        session.add(PlatformRoleAssignment(user_id=alternate.id, role_id=assignment.role_id))
        session.commit()
        current = build_tenant_context(session, alternate.id)
        a = promotion.promote(session, original, settings, first.id, _select_input(decision_a))
        session.commit()
        b = promotion.promote(session, current, settings, second.id, _select_input(decision_b, a))
        session.commit()
        assert a.state["status"] == b.state["status"] == "SELECTED"

        assignment.status = PlatformAssignmentStatus.SUSPENDED
        session.commit()
        try:
            view = promotion.promotion_detail(session, current, settings, second.id)
            assert "REVERT" not in view["allowed_actions"]
            assert "CLEAR_SELECTION" in view["allowed_actions"]
            revert_input = _revert_input(b)
            denied = promotion.revert(session, current, settings, second.id, revert_input)
            session.commit()
            assert denied.state["status"] == "DENIED"
            clear = revert_input.model_copy(
                update={
                    "operation_id": uuid.uuid4(),
                    "clear_selection": True,
                }
            )
            cleared = promotion.revert(session, current, settings, second.id, clear)
            session.commit()
            assert cleared.state["status"] == "REVERTED"
            assert cleared.state["reversion_mode"] == "CLEAR_SELECTION"
            assert cleared.state["selected"] is None
            assert cleared.previous_checksum == b.checksum
            assert cleared.state["previous"] == b.state["selected"]
            assert _selection_count(session, scenario.prompt.prompt_definition_id) == 3
            assert promotion.revert(session, current, settings, second.id, clear).id == cleared.id
            with pytest.raises(FeedbackConflict, match="OPERATION_CONFLICT"):
                promotion.revert(
                    session,
                    current,
                    settings,
                    second.id,
                    clear.model_copy(update={"clear_selection": False}),
                )
            session.rollback()
            assert (
                session.scalar(
                    select(AuditEvent.id).where(
                        AuditEvent.request_id == str(cleared.id),
                        AuditEvent.event_type == "feedback.prompt_selection_cleared",
                    )
                )
                is not None
            )
        finally:
            assignment.status = PlatformAssignmentStatus.ACTIVE
            session.commit()
