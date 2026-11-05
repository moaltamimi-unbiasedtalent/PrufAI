"""Phase 4A acceptance on the existing guarded, Alembic-migrated PostgreSQL fixture."""

from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.auth.api import auth_settings_dependency
from app.auth.service import AuthenticationService
from app.authorization import AuthorizationError, build_tenant_context
from app.config import Settings, get_settings
from app.db import get_db_session
from app.feedback import intake, proposals
from app.feedback.common import FeedbackConflict
from app.main import app
from app.models import AuditEvent, ResultAppealVersion, StandardPackVersion, User
from app.models.feedback import FeedbackOperation, FeedbackPattern, GovernedFeedback
from app.scripts.seed_demo import PASSWORD, reconcile_demo
from app.services.identity import IdentityService
from app.services.organisations import OrganisationService
from tests.alembic_helpers import repository_alembic_head
from tests.test_evaluation_regression_postgresql_integration import (
    _case,
    _promotion,
    _publish,
    _source,
    _suite,
)
from tests.test_expert_references_postgresql_integration import expert as expert
from tests.test_integrated_practice_postgresql_integration import (
    Journey,
    release_and_statement,
    submitted_and_scored,
)

REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="Governed feedback acceptance requires isolated Alembic PostgreSQL",
)


def _pin(row):
    return {"id": str(row.id), "checksum": row.checksum}


def _summary_pin(row):
    return {"id": row["id"], "checksum": row["checksum"]}


def _evaluation_input(run, result, **changes):
    return intake.FeedbackIntakeInput.model_validate(
        {
            "operation_id": str(uuid.uuid4()),
            "source": {
                "kind": "EVALUATION_FAILURE",
                "run": _pin(run),
                "result": _pin(result),
                "failed_check": "audio_identity",
            },
            "category": "AUDIO_EVIDENCE",
            "severity": "MEDIUM",
            **changes,
        }
    )


def _decision(item, action, reason_code, **changes):
    return intake.FeedbackDecisionInput.model_validate(
        {
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": item["checksum"],
            "action": action,
            "reason_code": reason_code,
            **changes,
        }
    )


def _receive(expert, session, run, result, **changes):
    return intake.intake_feedback(
        session, expert.context(session), _evaluation_input(run, result, **changes)
    )


def _triage(expert, session, item):
    return intake.decide_feedback(
        session,
        expert.context(session),
        uuid.UUID(item["id"]),
        _decision(item, "TRIAGE", "SOURCE_REVIEWED"),
    )


def _confirm(expert, session, item):
    return intake.decide_feedback(
        session,
        expert.context(session, "approver"),
        uuid.UUID(item["id"]),
        _decision(item, "CONFIRM", "REPRODUCED"),
    )


def _pattern(expert, session, anchor):
    return proposals.create_pattern(
        session,
        expert.context(session),
        proposals.PatternInput.model_validate(
            {"operation_id": str(uuid.uuid4()), "anchor": _summary_pin(anchor)}
        ),
    )


def _proposal_input(expert, pattern, **changes):
    return proposals.ProposalInput.model_validate(
        {
            "operation_id": str(uuid.uuid4()),
            "pattern": _summary_pin(pattern),
            "target": {
                "type": "STANDARD_PACK_VERSION",
                "id": str(expert.pack_id),
                "checksum": expert.pack_checksum,
            },
            "change_category": "VALIDATION_GUARD",
            "hypothesis_code": "PREVENT_RECORDED_FAILURE",
            **changes,
        }
    )


def _proposal_decision(proposal, **changes):
    return proposals.ProposalDecisionInput.model_validate(
        {
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": proposal["receipt"]["checksum"],
            "action": "APPROVE_FOR_EVALUATION",
            "reason_code": "EVALUATION_PLAN_SUFFICIENT",
            **changes,
        }
    )


def test_persisted_evaluation_receipt_idempotency_and_independent_confirmation(expert):
    case = _case()
    _, run, results = _source(expert, [case])
    result = results[case["case_id"]]
    payload = _evaluation_input(run, result, category="EVIDENCE_PROVENANCE")
    with expert.factory() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == (
            repository_alembic_head()
        )
        author = expert.context(session)
        received = intake.intake_feedback(session, author, payload)
        session.commit()
        assert received["status"] == "RECEIVED"
        assert received["source_id"] == str(result.id)
        assert received["source_checksum"] == result.checksum
        assert received["evidence_class"] == "TECHNICAL_TEST"
        assert received["execution_class"] == "TEST_ONLY"
        assert intake.intake_feedback(session, author, payload)["checksum"] == received["checksum"]
        with pytest.raises(FeedbackConflict):
            intake.intake_feedback(session, author, payload.model_copy(update={"severity": "HIGH"}))
        session.rollback()
        triaged = _triage(expert, session, received)
        session.commit()
        with pytest.raises(AuthorizationError):
            intake.decide_feedback(
                session,
                author,
                uuid.UUID(received["id"]),
                _decision(triaged, "CONFIRM", "REPRODUCED"),
            )
        session.rollback()
        with pytest.raises(FeedbackConflict):
            _confirm(expert, session, received)
        session.rollback()
        approved = _confirm(expert, session, triaged)
        session.commit()
        assert approved["status"] == "CONFIRMED"
        assert approved["checksum"] != received["checksum"]
        replay = intake.intake_feedback(session, author, payload)
        assert replay["checksum"] == received["checksum"] and replay["status"] == "RECEIVED"
        events = list(
            session.scalars(select(AuditEvent).where(AuditEvent.entity_id == received["id"]))
        )
        assert {event.event_type for event in events} == {
            "feedback.received",
            "feedback.classified",
            "feedback.confirmed",
        }
        assert str(payload.operation_id) in {event.request_id for event in events}
        row = session.get(GovernedFeedback, uuid.UUID(received["id"]))
        assert row is not None
        serialized = json.dumps([row.source, *[event.metadata_json for event in events]])
        assert case["input"]["text"] not in serialized
        assert case["reference"]["text"] not in serialized


def test_forged_or_unmeasured_evaluation_source_and_held_out_intake_are_denied(expert):
    case = _case()
    _, run, results = _source(expert, [case])
    result = results[case["case_id"]]
    held = _case(split="HELD_OUT")
    _, held_run, held_results = _source(expert, [held])
    with expert.factory() as session:
        author = expert.context(session)
        before = session.scalar(select(func.count()).select_from(GovernedFeedback))
        exact = _evaluation_input(run, result).model_dump(mode="json")
        forged = {
            **exact,
            "source": {**exact["source"], "result": {**_pin(result), "checksum": "0" * 64}},
        }
        with pytest.raises((ValueError, AuthorizationError)):
            intake.intake_feedback(
                session, author, intake.FeedbackIntakeInput.model_validate(forged)
            )
        session.rollback()
        with pytest.raises((ValueError, AuthorizationError)):
            intake.intake_feedback(
                session,
                author,
                intake.FeedbackIntakeInput.model_validate(
                    {**exact, "source": {**exact["source"], "failed_check": "language_compliance"}}
                ),
            )
        session.rollback()
        with pytest.raises((ValueError, AuthorizationError)):
            intake.intake_feedback(
                session, author, _evaluation_input(held_run, held_results[held["case_id"]])
            )
        session.rollback()
        assert session.scalar(select(func.count()).select_from(GovernedFeedback)) == before


def test_confirmed_group_exact_members_and_proposal_for_evaluation(expert):
    cases = [_case(), _case()]
    _, run, results = _source(expert, cases)
    with expert.factory() as session:
        author = expert.context(session)
        confirmed = [
            _confirm(
                expert,
                session,
                _triage(expert, session, _receive(expert, session, run, results[case["case_id"]])),
            )
            for case in cases
        ]
        pattern = _pattern(expert, session, confirmed[0])
        session.commit()
        assert pattern["member_count"] == 2
        assert pattern["distinct_source_count"] == 2
        stored = session.get(FeedbackPattern, uuid.UUID(pattern["id"]))
        assert stored is not None
        assert {member["id"] for member in stored.body["members"]} == {
            row["id"] for row in confirmed
        }
        assert {member["checksum"] for member in stored.body["members"]} == {
            row["checksum"] for row in confirmed
        }
        wrong_target = _proposal_input(
            expert,
            pattern,
            target={
                "type": "STANDARD_PACK_VERSION",
                "id": str(expert.pack_id),
                "checksum": "0" * 64,
            },
        )
        with pytest.raises(FeedbackConflict):
            proposals.create_proposal(session, author, wrong_target)
        session.rollback()
        proposal = proposals.create_proposal(session, author, _proposal_input(expert, pattern))
        session.commit()
        assert proposal["target"]["checksum"] == expert.pack_checksum
        assert proposal["status"] == "PENDING_REVIEW"
        assert proposal["release_readiness"] == "NOT_READY"
        assert "REQUIRE_EVALUATION" in str(proposal["evaluation_requirements"])
        with pytest.raises(AuthorizationError):
            proposals.decide_proposal(
                session, author, uuid.UUID(proposal["id"]), _proposal_decision(proposal)
            )
        session.rollback()
        with pytest.raises(AuthorizationError):
            proposals.decide_proposal(
                session,
                expert.context(session, "candidate"),
                uuid.UUID(proposal["id"]),
                _proposal_decision(proposal),
            )
        session.rollback()
        request = _proposal_decision(proposal)
        reviewer = expert.context(session, "approver")
        approved = proposals.decide_proposal(session, reviewer, uuid.UUID(proposal["id"]), request)
        session.commit()
        assert approved["status"] == "APPROVED_FOR_EVALUATION"
        assert approved["release_readiness"] == "NOT_READY"
        assert approved["production_activation"] is False
        assert (
            proposals.decide_proposal(session, reviewer, uuid.UUID(proposal["id"]), request)
            == approved
        )
        pack = session.get(StandardPackVersion, expert.pack_id)
        assert pack is not None and pack.configuration_checksum == expert.pack_checksum
        assert pack.status.value == "PUBLISHED"
        extra = _case()
        _, extra_run, extra_results = _source(expert, [extra])
        third = _receive(expert, session, extra_run, extra_results[extra["case_id"]])
        _confirm(expert, session, _triage(expert, session, third))
        updated = _pattern(expert, session, confirmed[0])
        session.commit()
        assert updated["version_number"] == pattern["version_number"] + 1
        assert updated["group_key"] == pattern["group_key"]
        assert updated["member_count"] == 3
        assert stored.body["member_count"] == 2
        assert (
            session.scalar(
                select(AuditEvent.event_type).where(AuditEvent.entity_id == updated["id"])
            )
            == "feedback.pattern_updated"
        )


def test_links_exact_existing_regression_suite_and_baseline(expert):
    case = _case()
    _, run, results = _source(expert, [case])
    result = results[case["case_id"]]
    from app.evaluation import regression_registry

    with expert.factory() as session:
        author = expert.context(session)
        promoted = regression_registry.promote_case(session, author, _promotion(run, result))
        published = _publish(expert, session, promoted)
        _, suite = _suite(expert, session, [published])
        item = _confirm(
            expert,
            session,
            _triage(
                expert,
                session,
                _receive(expert, session, run, result, category="TRANSCRIPTION_ERROR"),
            ),
        )
        pattern = _pattern(expert, session, item)
        proposal = proposals.create_proposal(
            session,
            author,
            _proposal_input(expert, pattern, suite_version=_pin(suite), baseline_run=_pin(run)),
        )
        session.commit()
        assert proposal["links"]["suite_version"] == _pin(suite)
        assert _summary_pin(proposal["links"]["baseline_run"]) == _pin(run)
        assert proposal["links"]["baseline_run"]["hard_gate"] == "FAIL"
        assert proposal["release_readiness"] == "NOT_READY"
        assert proposal["production_activation"] is False


def test_proposal_successor_preserves_reviewed_version_and_target(expert):
    case = _case()
    _, run, results = _source(expert, [case])
    with expert.factory() as session:
        author = expert.context(session)
        item = _receive(
            expert, session, run, results[case["case_id"]], category="OTHER_REVIEW_REQUIRED"
        )
        pattern = _pattern(
            expert, session, _confirm(expert, session, _triage(expert, session, item))
        )
        first = proposals.create_proposal(session, author, _proposal_input(expert, pattern))
        session.commit()
        successor = _proposal_input(expert, pattern, predecessor=_summary_pin(first))
        with pytest.raises(FeedbackConflict):
            proposals.create_proposal(session, author, successor)
        session.rollback()
        reviewer = expert.context(session, "approver")
        rejected = proposals.decide_proposal(
            session,
            reviewer,
            uuid.UUID(first["id"]),
            _proposal_decision(first, action="REJECT", reason_code="INSUFFICIENT_EVIDENCE"),
        )
        session.commit()
        with pytest.raises(AuthorizationError):
            proposals.create_proposal(session, reviewer, successor)
        session.rollback()
        next_version = proposals.create_proposal(session, author, successor)
        session.commit()
        assert rejected["status"] == "REJECTED"
        assert next_version["proposal_id"] == first["proposal_id"]
        assert next_version["version_number"] == 2
        assert next_version["checksum"] != first["checksum"]
        assert next_version["target"] == first["target"]
        assert next_version["status"] == "PENDING_REVIEW"


def _race(expert, contenders, command):
    barrier = Barrier(len(contenders))

    def run(actor):
        with expert.factory() as session:
            barrier.wait(timeout=10)
            try:
                command(session, expert.context(session, actor))
                session.commit()
            except FeedbackConflict:
                session.rollback()
                return "STALE"
            return "RECORDED"

    with ThreadPoolExecutor(max_workers=len(contenders)) as executor:
        return sorted(executor.map(run, contenders))


def test_concurrent_feedback_and_proposal_decisions_use_authoritative_receipts(expert):
    case = _case()
    _, run, results = _source(expert, [case])
    with expert.factory() as session:
        triaged = _triage(
            expert,
            session,
            _receive(
                expert, session, run, results[case["case_id"]], category="RUNTIME_RELIABILITY"
            ),
        )
        session.commit()
    feedback_id = uuid.UUID(triaged["id"])
    confirm = _decision(triaged, "CONFIRM", "REPRODUCED")
    reject = _decision(triaged, "NOT_CONFIRM", "NOT_REPRODUCIBLE")
    assert _race(
        expert,
        ("approver", "examiner-a"),
        lambda session, actor: intake.decide_feedback(
            session,
            actor,
            feedback_id,
            confirm if actor.user_id == expert.actors["approver"] else reject,
        ),
    ) == ["RECORDED", "STALE"]
    with expert.factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(FeedbackOperation)
                .where(FeedbackOperation.entity_id == feedback_id)
            )
            == 3
        )
        other = _case()
    _, other_run, other_results = _source(expert, [other])
    with expert.factory() as session:
        item = _confirm(
            expert,
            session,
            _triage(
                expert,
                session,
                _receive(
                    expert,
                    session,
                    other_run,
                    other_results[other["case_id"]],
                    category="MODEL_OUTPUT",
                ),
            ),
        )
        pattern = _pattern(expert, session, item)
        proposal = proposals.create_proposal(
            session, expert.context(session), _proposal_input(expert, pattern)
        )
        session.commit()
    proposal_id = uuid.UUID(proposal["id"])
    assert _race(
        expert,
        ("approver", "examiner-a"),
        lambda session, actor: proposals.decide_proposal(
            session, actor, proposal_id, _proposal_decision(proposal)
        ),
    ) == ["RECORDED", "STALE"]
    with expert.factory() as session:
        decisions = list(
            session.scalars(
                select(FeedbackOperation)
                .where(FeedbackOperation.entity_id == proposal_id)
                .order_by(FeedbackOperation.revision)
            )
        )
        assert len(decisions) == 2
        assert decisions[-1].state["status"] == "APPROVED_FOR_EVALUATION"
        assert decisions[-1].previous_checksum == decisions[0].checksum


def test_feedback_and_decision_history_reject_database_and_nested_json_rewrites(expert):
    case = _case()
    _, run, results = _source(expert, [case])
    with expert.factory() as session:
        item = _receive(expert, session, run, results[case["case_id"]])
        identifier = uuid.UUID(item["id"])
        session.commit()
        row = session.get(GovernedFeedback, identifier)
        assert row is not None
        row.source["source_checksum"] = "0" * 64
        with pytest.raises(ValueError, match="FEEDBACK_HISTORY_IMMUTABLE"):
            session.commit()
        session.rollback()
    with pytest.raises(DBAPIError, match="FEEDBACK_HISTORY_IMMUTABLE"):
        with expert.engine.begin() as connection:
            connection.execute(
                update(FeedbackOperation)
                .where(FeedbackOperation.entity_id == identifier)
                .values(state={"status": "CONFIRMED"})
            )


@pytest.fixture
def appeal_journey(expert):
    """Use the real practice journey inside the already guarded expert child DB."""
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_rate_limit_backend="memory",
        auth_rate_limit_login_attempts=100,
    )
    previous = dict(app.dependency_overrides)

    def database():
        with expert.factory() as session:
            yield session

    with expert.factory() as session:
        identifiers = reconcile_demo(
            session, Settings(_env_file=None, environment="development"), enabled=True
        )
        admin = session.scalar(
            select(User).where(User.normalized_email == "admin@demo.prufai.local")
        )
        assert admin is not None
        context = build_tenant_context(session, admin.id, uuid.UUID(identifiers["organisation_id"]))
        candidate = IdentityService.create_user(
            session,
            email=f"feedback-{uuid.uuid4()}@example.test",
            display_name="Synthetic Feedback Candidate",
        )
        AuthenticationService(settings).set_password(session, candidate, PASSWORD)
        membership = OrganisationService.create_membership(
            session, context, organisation_id=context.organisation_id, user_id=candidate.id
        )
        OrganisationService.assign_membership_role(
            session, context, membership_id=membership.id, role_key="EXAMINER"
        )
        session.commit()
        app.dependency_overrides[get_db_session] = database
        app.dependency_overrides[auth_settings_dependency] = lambda: settings
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            with TestClient(app) as client:
                yield Journey(
                    session,
                    client,
                    settings,
                    identifiers,
                    candidate.email,
                    candidate.id,
                    "",
                    admin.id,
                )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)


def test_actual_adjudicated_appeal_pin_stays_a_private_observation(appeal_journey):
    journey = appeal_journey
    state = release_and_statement(journey, submitted_and_scored(journey))
    version_id = state["result"]["result_version_id"]
    marker = "Synthetic appeal rationale private " + uuid.uuid4().hex
    appeal = journey.request(
        "POST",
        f"/api/v1/candidate/results/{version_id}/appeals",
        state["candidate"],
        json={"category": "SCORING", "grounds": marker, "operation_id": str(uuid.uuid4())},
    )
    path = journey.org + f"/appeals/{appeal['id']}"
    journey.request("POST", path + "/review", state["admin"])
    reviewed = journey.request("GET", path, state["admin"])
    decision = journey.request(
        "POST",
        path + "/adjudicate",
        state["admin"],
        json={
            "appeal_version_number": reviewed["current_version_number"],
            "appeal_checksum": reviewed["current_checksum"],
            "evidence_bundle_checksum": reviewed["evidence_bundle_checksum"],
            "decision": "REJECT",
            "reason": marker,
            "operation_id": str(uuid.uuid4()),
        },
    )
    appeal_versions = list(
        journey.session.scalars(
            select(ResultAppealVersion)
            .where(ResultAppealVersion.appeal_id == uuid.UUID(appeal["id"]))
            .order_by(ResultAppealVersion.version_number)
        )
    )
    assert decision["status"] == "ADJUDICATED_REJECTED"
    final_version = appeal_versions[-1]
    detail = journey.request("GET", path, state["admin"])
    assert final_version.checksum == detail["current_checksum"]
    feedback_path = journey.org + "/feedback"

    def payload(version):
        return {
            "operation_id": str(uuid.uuid4()),
            "source": {"kind": "GOVERNED_APPEAL_OUTCOME", "appeal_version": _pin(version)},
            "category": "SCORING_CORRECTNESS",
            "severity": "MEDIUM",
        }

    rejected = journey.client.post(
        feedback_path, headers=state["admin"], json=payload(appeal_versions[0])
    )
    assert rejected.status_code in {409, 422}
    item = journey.request(
        "POST", feedback_path, state["admin"], expected=201, json=payload(final_version)
    )
    assert item["source_id"] == str(final_version.id)
    assert item["source_checksum"] == final_version.checksum
    assert item["evidence_class"] == "UNLABELLED_OBSERVATION"
    assert item["privacy_class"] == "INSTITUTION_RESTRICTED"
    assert marker not in json.dumps(item)
    scope = journey.request("GET", feedback_path + f"?source_id={final_version.id}", state["admin"])
    assert [row["id"] for row in scope["items"]] == [item["id"]]
    stored = journey.session.get(GovernedFeedback, uuid.UUID(item["id"]))
    assert stored is not None and marker not in json.dumps(stored.source)
    platform = journey.login("platform.admin@demo.prufai.local", tenant=False)
    journey.request(
        "GET",
        f"/api/v1/platform/control-center/feedback/items/{item['id']}",
        platform,
        expected=404,
    )
