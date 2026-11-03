"""Required HUMAN Writing submission-to-statement proof on migrated PostgreSQL.

The foundation fixture creates valid versioned prerequisites through the real
services. Every assessed outcome below is produced through authenticated APIs.
No successful Writing outcome, AI run, provider or worker is manufactured.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select

from app.authorization import build_tenant_context
from app.models import (
    AIRun,
    AssessmentResult,
    CandidateExamAttempt,
    CertificateRecord,
    HumanReviewRequest,
    ItemScore,
    MembershipRole,
    OrganisationMembership,
    Permission,
    ResultCorrection,
    ResultRelease,
    ResultVersion,
    Role,
    RolePermission,
    ScoringRun,
    StatementOfResults,
    SubjectiveAssessmentVersion,
    User,
)
from app.models.enums import RoleScope
from app.scripts.seed_demo import PLATFORM_ADMIN_EMAIL
from app.scripts.seed_writing_demo import WRITING_CRITERIA, WRITING_LEVELS
from app.writing_assessment.service import canonical_checksum
from tests.test_writing_foundation_postgresql_integration import (
    WritingJourney,
    criteria,
)
from tests.test_writing_foundation_postgresql_integration import (
    writing as writing,
)

REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="POSTGRES_INTEGRATION_URL is required for the integrated HUMAN Writing journey",
)


def _result(writing: WritingJourney, attempt_id: str, actor: dict[str, str]) -> dict[str, Any]:
    writing.session.expire_all()
    record = writing.session.scalar(
        select(AssessmentResult).where(AssessmentResult.attempt_id == uuid.UUID(attempt_id))
    )
    assert record is not None
    return writing.request("GET", writing.org + f"/results/{record.id}", actor)


def _human_journey(writing: WritingJourney, *, approve: bool = True) -> dict[str, Any]:
    before_ai = writing.session.scalar(select(func.count()).select_from(AIRun))
    rubric = writing.publish(writing.rubric())
    submitted = writing.submit()
    admin = writing.login("admin@demo.prufai.local")
    reviewer = writing.login("reviewer@demo.prufai.local")
    body = {
        "operation_id": str(uuid.uuid4()),
        "attempt_id": submitted["attempt"]["id"],
        "exam_version_item_binding_id": submitted["item"]["binding_id"],
        "rubric_version_id": rubric["id"],
        "rubric_checksum": rubric["checksum"],
        "response_checksum": submitted["revision"].payload_checksum,
    }
    created = writing.request(
        "POST", writing.org + "/subjective-assessments/human", reviewer, json=body
    )
    assert (
        writing.request("POST", writing.org + "/subjective-assessments/human", reviewer, json=body)
        == created
    )
    proposal = {
        "operation_id": str(uuid.uuid4()),
        "expected_checksum": created["checksum"],
        "criteria": criteria(),
    }
    path = writing.org + f"/subjective-assessments/{created['version_id']}"
    proposed = writing.request("POST", path + "/human-proposal", reviewer, json=proposal)
    assert writing.request("POST", path + "/human-proposal", reviewer, json=proposal) == proposed
    state = {
        **submitted,
        "rubric": rubric,
        "created": created,
        "proposed": proposed,
        "admin": admin,
        "reviewer": reviewer,
        "assessment_path": path,
        "before_ai": before_ai,
    }
    if approve:
        state["approved"] = _approve(writing, state)
    return state


def _approve(writing: WritingJourney, state: dict[str, Any]) -> dict[str, Any]:
    body = {"operation_id": str(uuid.uuid4()), "expected_checksum": state["proposed"]["checksum"]}
    writing.request(
        "POST",
        state["assessment_path"] + "/human-approve",
        state["reviewer"],
        expected=403,
        json=body,
    )
    wrong = {**body, "operation_id": str(uuid.uuid4()), "expected_checksum": "0" * 64}
    writing.request(
        "POST",
        state["assessment_path"] + "/human-approve",
        state["admin"],
        expected=409,
        json=wrong,
    )
    approved = writing.request(
        "POST", state["assessment_path"] + "/human-approve", state["admin"], json=body
    )
    assert (
        writing.request(
            "POST", state["assessment_path"] + "/human-approve", state["admin"], json=body
        )
        == approved
    )
    assert approved["status"] == "APPROVED" and approved["assessment_origin"] == "HUMAN"
    return approved


def _score(writing: WritingJourney, state: dict[str, Any]) -> dict[str, Any]:
    path = writing.org + f"/exam-attempts/{state['attempt']['id']}/score"
    run = writing.request("POST", path, state["admin"])
    assert writing.request("POST", path, state["admin"]) == run
    projection = _result(writing, state["attempt"]["id"], state["admin"])
    assert projection["scoring_run_id"] == run["id"]
    return {**state, "run": run, "result": projection}


def _release(writing: WritingJourney, state: dict[str, Any]) -> dict[str, Any]:
    result = state["result"]
    path = writing.org + f"/result-versions/{result['result_version_id']}"
    operation = {"operation_id": str(uuid.uuid4())}
    writing.request("POST", path + "/release", state["admin"], expected=409, json=operation)
    review = writing.request("POST", path + "/request-review", state["admin"])
    assert review["status"] == "PENDING" and review["reviewed_checksum"] == result["checksum"]
    writing.request("POST", path + "/approve", state["admin"])
    released = writing.request("POST", path + "/release", state["admin"], json=operation)
    assert writing.request("POST", path + "/release", state["admin"], json=operation) == released
    statement_operation = {"operation_id": str(uuid.uuid4())}
    statement_path = writing.org + f"/releases/{released['release_id']}/statement"
    statement = writing.request("POST", statement_path, state["admin"], json=statement_operation)
    assert (
        writing.request("POST", statement_path, state["admin"], json=statement_operation)
        == statement
    )
    return {
        **state,
        "release": released,
        "statement": statement,
        "release_operation": operation,
        "statement_operation": statement_operation,
        "release_path": path + "/release",
        "statement_path": statement_path,
    }


def _component(writing: WritingJourney, state: dict[str, Any]) -> dict[str, Any]:
    writing.session.expire_all()
    version = writing.session.get(
        SubjectiveAssessmentVersion, uuid.UUID(state["created"]["version_id"])
    )
    assert version is not None and version.status.value == "APPROVED"
    assert version.assessment_origin == "HUMAN"
    assert all(
        getattr(version, key) is None
        for key in (
            "ai_run_id",
            "workflow_version_id",
            "model_profile_id",
            "agent_version_id",
            "prompt_version_id",
        )
    )
    assert version.ai_proposal == {} and all(
        row.confidence is None for row in version.criterion_assessments
    )
    return {
        "skill": "WRITING",
        "assessment_origin": "HUMAN",
        "subjective_assessment_version_id": str(version.id),
        "subjective_assessment_checksum": version.checksum,
        "rubric_version_id": str(version.rubric_version_id),
        "rubric_checksum": version.rubric_checksum,
        "candidate_response_revision_id": str(version.candidate_response_revision_id),
        "response_checksum": version.response_checksum,
        "task_spec_id": state["rubric"]["task_spec_id"],
        "approved_score": str(version.approved_score),
        "maximum_points": "6.0000",
    }


def _assert_public(payload: Any, state: dict[str, Any]) -> None:
    encoded = json.dumps(payload, default=str)
    assert state["text"] not in encoded
    for criterion in criteria():
        assert criterion["rationale"] not in encoded
        for quote in criterion["evidence"]:
            assert quote["quote"] not in encoded
    assert not any(
        f'"{key}"' in encoded
        for key in (
            "rationale",
            "evidence",
            "candidate_text",
            "ai_run_id",
            "confidence",
            "private_url",
        )
    )


def test_real_human_writing_journey_pins_scoring_release_statement_and_safe_audit(
    writing: WritingJourney,
):
    state = _release(writing, _score(writing, _human_journey(writing)))
    component = _component(writing, state)
    result = state["result"]
    assert result["status"] == "REVIEW_REQUIRED" and result["outcome"] == "UNDETERMINED"
    assert Decimal(result["raw_awarded_points"]) == 4 and Decimal(result["raw_maximum_points"]) == 6
    assert result["assessment_components"] == [component]
    run = writing.session.get(ScoringRun, uuid.UUID(state["run"]["id"]))
    score = writing.session.scalar(select(ItemScore).where(ItemScore.scoring_run_id == run.id))
    assert run.standard_pack_version_id == uuid.UUID(writing.identifiers["writing_pack_version_id"])
    assert str(run.exam_version_id) == writing.exam_version_id
    assert (
        run.candidate_user_id == writing.candidate_id
        and run.checksum == result["scoring_run_checksum"]
    )
    assert score.candidate_response_revision_id == state["revision"].id
    assert score.response_checksum == state["revision"].payload_checksum
    assert (
        score.scoring_metadata["subjective_assessment_version_id"]
        == component["subjective_assessment_version_id"]
    )
    assert (
        score.scoring_metadata["subjective_assessment_checksum"]
        == component["subjective_assessment_checksum"]
    )
    assert score.scoring_metadata["component"] == component
    approved_criteria = score.scoring_metadata["approved_criteria"]
    assert {row["criterion_code"]: Decimal(row["points"]) for row in approved_criteria} == {
        "TASK": Decimal("2"),
        "LANGUAGE": Decimal("2"),
    }
    assert all(set(row) == {"criterion_code", "level", "points"} for row in approved_criteria)
    assert score.awarded_points == 4 and score.maximum_points == 6
    released = writing.request(
        "GET", f"/api/v1/candidate/releases/{state['release']['release_id']}", state["candidate"]
    )
    assert released["result"]["assessment_components"] == [component]
    assert released["candidate_user_id"] == str(writing.candidate_id)
    assert released["attempt_id"] == state["attempt"]["id"]
    statement = writing.request(
        "GET",
        f"/api/v1/candidate/releases/{state['release']['release_id']}/statement",
        state["candidate"],
    )
    assert statement == state["statement"]
    payload = statement["payload"]
    assert payload["classification"] == "PRACTICE" and payload["outcome"] == "UNDETERMINED"
    assert payload["assessment_components"] == [component]
    assert payload["result_version_id"] == result["result_version_id"]
    assert payload["scoring_run_id"] == str(run.id)
    assert payload["result_checksum"] == result["checksum"]
    assert payload["standard_pack_version_id"] == str(run.standard_pack_version_id)
    assert payload["candidate_user_id"] == str(writing.candidate_id)
    assert payload["attempt_id"] == state["attempt"]["id"]
    persisted_release = writing.session.get(
        ResultRelease, uuid.UUID(state["release"]["release_id"])
    )
    assert payload["issued_by_user_id"] == str(persisted_release.released_by_user_id)
    assert payload["organisation_id"] == writing.identifiers["organisation_id"]
    assert payload["release_checksum"] == persisted_release.checksum
    assert statement["issued_at"] and payload["issued_at"] == statement["issued_at"]
    assert (
        statement["checksum"]
        == hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
    )
    _assert_public(released, state)
    _assert_public(statement, state)
    platform = writing.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    event_ids = {
        "rubric.published": state["rubric"]["id"],
        "subjective_assessment.human_initialized": state["created"]["version_id"],
        "subjective_assessment.human_proposed": state["created"]["version_id"],
        "subjective_assessment.human_approved": state["created"]["version_id"],
        "scoring.run.completed": str(run.id),
        "result.version.created": result["result_version_id"],
        "result.release_created": state["release"]["release_id"],
        "statement.created": statement["id"],
    }
    for event_type, entity_id in event_ids.items():
        page = writing.request(
            "GET",
            "/api/v1/platform/control-center/audit",
            platform,
            params={
                "organisation_id": writing.identifiers["organisation_id"],
                "event_type": event_type,
                "limit": 100,
            },
        )
        row = next(item for item in page["items"] if item["entity_id"] == entity_id)
        assert row["event_type"] == event_type and row["entity_type"] != "REDACTED"
        _assert_public(page, state)
    assert writing.session.scalar(select(func.count()).select_from(AIRun)) == state["before_ai"]


def test_unapproved_writing_stays_incomplete_then_creates_a_new_exact_approved_scoring_run(
    writing: WritingJourney,
):
    state = _human_journey(writing, approve=False)
    pending = _score(writing, state)
    assert pending["result"]["status"] == "INCOMPLETE"
    result_path = writing.org + f"/result-versions/{pending['result']['result_version_id']}"
    writing.request("POST", result_path + "/request-review", state["admin"], expected=409)
    writing.request(
        "POST",
        result_path + "/release",
        state["admin"],
        expected=409,
        json={"operation_id": str(uuid.uuid4())},
    )
    assert writing.request("GET", "/api/v1/candidate/results", state["candidate"]) == []
    state["approved"] = _approve(writing, state)
    scored = _score(writing, state)
    assert scored["run"]["id"] != pending["run"]["id"]
    assert scored["result"]["result_version_id"] != pending["result"]["result_version_id"]
    assert Decimal(scored["result"]["raw_awarded_points"]) == 4
    assert scored["result"]["assessment_components"] == [_component(writing, scored)]
    writing.request("POST", result_path + "/approve", state["admin"], expected=409)
    writing.session.expire_all()
    old = writing.session.get(ResultVersion, uuid.UUID(pending["result"]["result_version_id"]))
    assert old.checksum == pending["result"]["checksum"]
    assert old.status.value == "INCOMPLETE"
    writing.request(
        "POST",
        result_path + "/release",
        state["admin"],
        expected=409,
        json={"operation_id": str(uuid.uuid4())},
    )
    # This declarative AI policy fixture executes no model. The HUMAN endpoint
    # must refuse to become an automatic fallback for an AI-assisted rubric.
    identity = writing.request(
        "POST",
        writing.org + "/rubrics",
        state["admin"],
        json={
            "code": "NO_FALLBACK_" + uuid.uuid4().hex,
            "name": "SYNTHETIC AI policy refusal fixture",
        },
    )
    ai_rubric = writing.request(
        "POST",
        writing.org + f"/rubrics/{identity['id']}/versions",
        state["admin"],
        json={
            "standard_pack_version_id": writing.identifiers["writing_pack_version_id"],
            "task_spec_id": writing.identifiers["writing_task_spec_id"],
            "version_label": "explicit-ai-policy-v1",
            "criteria": WRITING_CRITERIA,
            "performance_levels": WRITING_LEVELS,
            "aggregation_policy": {"method": "SUM"},
            "evidence_policy": {"required": False},
            "ai_assessment_policy": {"enabled": True, "mode": "AI_ASSISTED"},
            "human_review_policy": {"required": True, "separation_of_duties": True},
        },
    )
    ai_rubric["rubric_id"] = identity["id"]
    writing.deliverable(ai_rubric)
    ai_rubric = writing.publish(ai_rubric)
    ai_state = writing.submit()
    failure = writing.request(
        "POST",
        writing.org + "/subjective-assessments/human",
        state["reviewer"],
        expected=409,
        json={
            "operation_id": str(uuid.uuid4()),
            "attempt_id": ai_state["attempt"]["id"],
            "exam_version_item_binding_id": ai_state["item"]["binding_id"],
            "rubric_version_id": ai_rubric["id"],
            "rubric_checksum": ai_rubric["checksum"],
            "response_checksum": ai_state["revision"].payload_checksum,
        },
    )
    assert "HUMAN_ONLY" in failure["detail"]
    writing.session.expire_all()
    assert (
        writing.session.scalar(
            select(func.count())
            .select_from(SubjectiveAssessmentVersion)
            .where(SubjectiveAssessmentVersion.attempt_id == uuid.UUID(ai_state["attempt"]["id"]))
        )
        == 0
    )
    assert writing.session.scalar(select(func.count()).select_from(AIRun)) == state["before_ai"]


def test_full_writing_artifacts_remain_candidate_owned_and_tenant_scoped(writing: WritingJourney):
    state = _release(writing, _score(writing, _human_journey(writing)))
    other = writing.login(writing.other_email)
    for path in (
        f"/api/v1/candidate/results/{state['result']['result_id']}",
        f"/api/v1/candidate/releases/{state['release']['release_id']}",
        f"/api/v1/candidate/releases/{state['release']['release_id']}/statement",
    ):
        writing.request("GET", path, other, expected=404)
    foreign = writing.login(writing.foreign_email, organisation_id=str(writing.foreign_org_id))
    writing.request(
        "POST", state["release_path"], foreign, expected=403, json=state["release_operation"]
    )
    foreign_path = (
        f"/api/v1/organisations/{writing.foreign_org_id}/releases/{state['release']['release_id']}"
    )
    writing.request("GET", foreign_path, foreign, expected=404)
    writing.request(
        "POST",
        writing.org + f"/exam-attempts/{state['attempt']['id']}/score",
        state["candidate"],
        expected=403,
    )
    writing.request(
        "POST",
        state["release_path"],
        state["candidate"],
        expected=403,
        json=state["release_operation"],
    )
    writing.request("GET", "/api/v1/platform/control-center/audit", state["admin"], expected=403)
    writing.request(
        "GET",
        writing.org + f"/subjective-assessments/{state['created']['id']}",
        state["candidate"],
        expected=403,
    )


def test_scoring_release_and_statement_replays_preserve_immutable_human_history(
    writing: WritingJourney,
):
    state = _release(writing, _score(writing, _human_journey(writing)))
    writing.session.expire_all()
    version = writing.session.get(
        SubjectiveAssessmentVersion, uuid.UUID(state["created"]["version_id"])
    )
    artifact = writing.session.get(StatementOfResults, uuid.UUID(state["statement"]["id"]))
    before = (version.checksum, copy.deepcopy(artifact.payload), artifact.checksum)
    for mutate in (
        lambda: setattr(version.criterion_assessments[0], "approved_points", Decimal("99")),
        lambda: version.criterion_assessments[0].evidence[0].update({"quote": "rewritten"}),
        lambda: setattr(artifact, "payload", {**artifact.payload, "raw_awarded_points": "99"}),
    ):
        with pytest.raises(ValueError, match="immutable"):
            mutate()
            writing.session.commit()
        writing.session.rollback()
    assert (version.checksum, artifact.payload, artifact.checksum) == before
    writing.request(
        "POST",
        state["release_path"],
        state["admin"],
        expected=409,
        json={"operation_id": str(uuid.uuid4())},
    )
    writing.request(
        "POST",
        state["statement_path"],
        state["admin"],
        expected=409,
        json={"operation_id": str(uuid.uuid4())},
    )
    assert (
        writing.request(
            "POST", state["release_path"], state["admin"], json=state["release_operation"]
        )
        == state["release"]
    )
    assert (
        writing.request(
            "POST", state["statement_path"], state["admin"], json=state["statement_operation"]
        )
        == state["statement"]
    )
    assert (
        writing.request(
            "POST", writing.org + f"/exam-attempts/{state['attempt']['id']}/score", state["admin"]
        )["id"]
        == state["run"]["id"]
    )
    for model, column, identifier in (
        (
            SubjectiveAssessmentVersion,
            SubjectiveAssessmentVersion.attempt_id,
            state["attempt"]["id"],
        ),
        (ScoringRun, ScoringRun.attempt_id, state["attempt"]["id"]),
        (ResultRelease, ResultRelease.attempt_id, state["attempt"]["id"]),
        (StatementOfResults, StatementOfResults.attempt_id, state["attempt"]["id"]),
    ):
        assert (
            writing.session.scalar(
                select(func.count()).select_from(model).where(column == uuid.UUID(identifier))
            )
            == 1
        )
    assert (
        writing.session.scalar(
            select(func.count())
            .select_from(CertificateRecord)
            .where(CertificateRecord.candidate_user_id == writing.candidate_id)
        )
        == 0
    )
    assert (
        writing.session.get(CandidateExamAttempt, uuid.UUID(state["attempt"]["id"])).status.value
        == "SUBMITTED"
    )


def test_upheld_writing_reassessment_creates_exact_successors_without_rewriting_released_history(
    writing: WritingJourney,
):
    state = _release(writing, _score(writing, _human_journey(writing)))
    original_statement = copy.deepcopy(state["statement"])
    writing.session.expire_all()
    source = writing.session.get(
        SubjectiveAssessmentVersion, uuid.UUID(state["created"]["version_id"])
    )
    original_checksum = source.checksum
    original_values = copy.deepcopy(source.human_approved_values)
    org_id = uuid.UUID(writing.identifiers["organisation_id"])
    assessor = writing.session.scalar(
        select(User).where(User.email == "reviewer@demo.prufai.local")
    )
    administrator = writing.session.scalar(
        select(User).where(User.email == "admin@demo.prufai.local")
    )
    required = {"appeal.correct", "subjective.review"}
    context = build_tenant_context(writing.session, assessor.id, org_id)
    missing = required - context.permissions
    if missing:
        # A must possess the actual correction capability; a missing-role 403
        # would not prove independent human judgment is enforced.
        role = Role(key="WRITING_CORRECTOR_TEST_" + uuid.uuid4().hex, scope=RoleScope.TENANT)
        writing.session.add(role)
        writing.session.flush()
        for key in missing:
            # Shared capability keys have one vocabulary row; the actual role
            # assignment determines whether the caller has tenant authority.
            permission = writing.session.scalar(select(Permission).where(Permission.key == key))
            assert permission is not None
            writing.session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        membership = writing.session.scalar(
            select(OrganisationMembership).where(
                OrganisationMembership.user_id == assessor.id,
                OrganisationMembership.organisation_id == org_id,
            )
        )
        assert membership is not None
        writing.session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
        writing.session.commit()
    for actor in (assessor, administrator):
        context = build_tenant_context(writing.session, actor.id, org_id)
        assert context.organisation_id == org_id and context.membership_id is not None
        assert required <= context.permissions
        assert actor.id != writing.candidate_id
    assessor_headers = writing.login(assessor.email)
    appeal = writing.request(
        "POST",
        f"/api/v1/candidate/results/{state['result']['result_version_id']}/appeals",
        state["candidate"],
        json={
            "category": "WRITING",
            "grounds": "Original synthetic practice: request criterion review.",
            "operation_id": str(uuid.uuid4()),
        },
    )
    path = writing.org + f"/appeals/{appeal['id']}"
    writing.request("POST", path + "/review", state["admin"])
    detail = writing.request("GET", path, state["admin"])
    writing.request(
        "POST",
        path + "/adjudicate",
        state["admin"],
        json={
            "appeal_version_number": detail["current_version_number"],
            "appeal_checksum": detail["current_checksum"],
            "evidence_bundle_checksum": detail["evidence_bundle_checksum"],
            "decision": "UPHOLD",
            "operation_id": str(uuid.uuid4()),
            "reason": "Independent HUMAN reassessment is justified for this synthetic response.",
        },
    )
    upheld = writing.request("GET", path, state["admin"])
    assert upheld["status"] == "ADJUDICATED_UPHELD"
    operation = str(uuid.uuid4())
    correction_body = {
        "appeal_version_number": upheld["current_version_number"],
        "appeal_checksum": upheld["current_checksum"],
        "evidence_bundle_checksum": upheld["evidence_bundle_checksum"],
        "correction_type": "WRITING_REASSESSMENT",
        "operation_id": operation,
        "corrections": {"LANGUAGE": {"approved_points": "3", "approved_level": "CLEAR"}},
    }
    writing.request(
        "POST", path + "/correction", assessor_headers, expected=403, json=correction_body
    )
    assert (
        writing.session.scalar(
            select(func.count())
            .select_from(ResultCorrection)
            .where(ResultCorrection.appeal_id == uuid.UUID(appeal["id"]))
        )
        == 0
    )
    corrected = writing.request("POST", path + "/correction", state["admin"], json=correction_body)
    assert corrected["status"] == "RESCORED"
    writing.request(
        "POST", path + "/correction", assessor_headers, expected=403, json=correction_body
    )
    altered = copy.deepcopy(correction_body)
    altered["corrections"]["LANGUAGE"]["approved_points"] = "2"
    writing.request("POST", path + "/correction", state["admin"], expected=409, json=altered)
    assert (
        writing.request("POST", path + "/correction", state["admin"], json=correction_body)
        == corrected
    )
    writing.session.expire_all()
    correction = writing.session.get(ResultCorrection, uuid.UUID(corrected["id"]))
    successor = writing.session.get(
        SubjectiveAssessmentVersion, correction.new_component_version_id
    )
    assert correction.source_result_version_id == uuid.UUID(state["result"]["result_version_id"])
    assert correction.source_result_checksum == state["result"]["checksum"]
    assert correction.created_by_user_id == administrator.id
    assert successor.supersedes_version_id == source.id and successor.assessment_origin == "HUMAN"
    assert successor.status.value == "APPROVED" and successor.approved_score == 5
    assert successor.candidate_response_revision_id == source.candidate_response_revision_id
    assert successor.response_checksum == source.response_checksum
    assert successor.rubric_version_id == source.rubric_version_id
    assert successor.rubric_checksum == source.rubric_checksum
    assert all(
        getattr(successor, key) is None
        for key in (
            "ai_run_id",
            "workflow_version_id",
            "model_profile_id",
            "agent_version_id",
            "prompt_version_id",
        )
    )
    review = writing.session.scalar(
        select(HumanReviewRequest).where(
            HumanReviewRequest.review_type == "RESULT_CORRECTION",
            HumanReviewRequest.entity_id == source.id,
        )
    )
    assert review is not None and review.status.value == "APPROVED"
    assert review.decided_by == administrator.id and review.decided_by != assessor.id
    assert review.reviewed_checksum == original_checksum
    normalized = {"LANGUAGE": {"approved_points": "3.0000", "approved_level": "CLEAR"}}
    assert successor.provenance["result_correction_id"] == str(correction.id)
    assert successor.provenance["correction_review_id"] == str(review.id)
    assert successor.provenance["source_version_id"] == str(source.id)
    assert successor.provenance["source_checksum"] == original_checksum
    assert successor.provenance["correction_input"] == normalized
    assert successor.provenance["assessed_by"] == str(assessor.id)
    assert successor.provenance["reviewed_by"] == str(administrator.id)
    assert successor.provenance["reviewed_at"]
    assert successor.checksum == canonical_checksum(
        {
            "source_checksum": original_checksum,
            "approved_score": str(successor.approved_score),
            "corrections": normalized,
            "supersedes_version_id": str(source.id),
        }
    )
    assert source.checksum == original_checksum and source.human_approved_values == original_values
    assert source.approved_score == 4
    result = writing.session.get(ResultVersion, correction.new_result_version_id)
    run = writing.session.get(ScoringRun, correction.new_scoring_run_id)
    assert str(run.id) != state["run"]["id"] and result.id != uuid.UUID(
        state["result"]["result_version_id"]
    )
    assert result.scoring_run_id == run.id and result.status.value == "REVIEW_REQUIRED"
    assert result.supersedes_version_id == uuid.UUID(state["result"]["result_version_id"])
    assert result.raw_awarded_points == 5 and result.raw_maximum_points == 6
    assert result.outcome.value == "UNDETERMINED"
    assert run.item_scores[0].scoring_metadata["subjective_assessment_version_id"] == str(
        successor.id
    )
    assert (
        run.item_scores[0].scoring_metadata["subjective_assessment_checksum"] == successor.checksum
    )
    writing.request(
        "POST",
        writing.org + f"/result-versions/{result.id}/release",
        state["admin"],
        expected=409,
        json={"operation_id": str(uuid.uuid4())},
    )
    old_statement = writing.request(
        "GET",
        f"/api/v1/candidate/releases/{state['release']['release_id']}/statement",
        state["candidate"],
    )
    assert old_statement["payload"] == original_statement["payload"]
    assert old_statement["checksum"] == original_statement["checksum"]
    for model in (ResultRelease, StatementOfResults):
        assert (
            writing.session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.attempt_id == uuid.UUID(state["attempt"]["id"]))
            )
            == 1
        )
    assert (
        writing.session.scalar(
            select(func.count())
            .select_from(CertificateRecord)
            .where(CertificateRecord.candidate_user_id == writing.candidate_id)
        )
        == 0
    )
    assert writing.session.scalar(select(func.count()).select_from(AIRun)) == state["before_ai"]
