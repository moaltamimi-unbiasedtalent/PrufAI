"""Actual HUMAN/AUDIO Speaking scoring and release on migrated PostgreSQL/private S3.

Technical generated microphone-equivalent tone bytes prove transport and governed
workflow only. They do not prove German speech or linguistic scoring accuracy.
Every assessment, result, release and statement is produced by real services.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (
    AIRun,
    AssessmentResult,
    CandidateMediaAsset,
    CandidateResponseRevision,
    ItemScore,
    ResultCorrection,
    ResultRelease,
    ResultVersion,
    RubricVersion,
    ScoringRun,
    StatementOfResults,
    SubjectiveAssessmentVersion,
    SubjectiveCriterionAssessment,
)
from app.scripts.seed_demo import PLATFORM_ADMIN_EMAIL
from tests.integration_support.audio_evidence import technical_tone_wav
from tests.test_human_speaking_postgresql_minio_integration import (
    speaking as speaking,
)

REQUIRED = (
    os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
    or os.getenv("PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION") == "1"
)
pytestmark = pytest.mark.skipif(
    not (os.getenv("POSTGRES_INTEGRATION_URL") and os.getenv("OBJECT_STORAGE_ENDPOINT"))
    and not REQUIRED,
    reason="Speaking results require real PostgreSQL and private MinIO/S3",
)


def _human(speaking, *, approve=True):
    state = speaking.submitted()
    created = speaking.initialize(state)
    proposed = speaking.propose(state, created)
    state.update(created=created, proposed=proposed)
    if approve:
        state["approved"] = speaking.approve(proposed)
    return state


def _score(speaking, state, *, expected=200, headers=None):
    return speaking.call(
        "POST",
        speaking.org + f"/exam-attempts/{state['id']}/score",
        headers or speaking.login("admin@demo.prufai.local"),
        expected=expected,
    )


def _result(speaking, state):
    speaking.session.expire_all()
    record = speaking.session.scalar(
        select(AssessmentResult).where(AssessmentResult.attempt_id == uuid.UUID(state["id"]))
    )
    assert record is not None
    return speaking.call(
        "GET", speaking.org + f"/results/{record.id}", speaking.login("admin@demo.prufai.local")
    )


def _release(speaking, state):
    run = _score(speaking, state)
    result = _result(speaking, state)
    admin = speaking.login("admin@demo.prufai.local")
    path = speaking.org + f"/result-versions/{result['result_version_id']}"
    operation = {"operation_id": str(uuid.uuid4())}
    speaking.call("POST", path + "/release", admin, expected=409, json=operation)
    speaking.call("POST", path + "/request-review", admin)
    speaking.call("POST", path + "/approve", admin)
    released = speaking.call("POST", path + "/release", admin, json=operation)
    assert speaking.call("POST", path + "/release", admin, json=operation) == released
    statement_path = speaking.org + f"/releases/{released['release_id']}/statement"
    statement_operation = {"operation_id": str(uuid.uuid4())}
    statement = speaking.call("POST", statement_path, admin, json=statement_operation)
    assert speaking.call("POST", statement_path, admin, json=statement_operation) == statement
    return {
        **state,
        "run": run,
        "result": result,
        "release": released,
        "statement": statement,
        "release_path": path + "/release",
        "release_operation": operation,
        "statement_path": statement_path,
        "statement_operation": statement_operation,
    }


def _public(value, speaking, state):
    encoded = json.dumps(value, default=str)
    for private in (
        speaking.asset(state["asset"]).storage_key,
        "Private HUMAN technical audio acceptance rationale.",
        '"rationale"',
        '"start_ms"',
        '"end_ms"',
        '"storage_key"',
        '"private_url"',
        '"ai_proposal"',
        '"transcript"',
    ):
        assert private not in encoded


def _successor(speaking, state):
    return speaking.action(
        state["approved"],
        "human-successor",
        email="admin@demo.prufai.local",
        body={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": state["approved"]["checksum"],
            "reason": "Fresh independently governed technical-audio reassessment.",
        },
    )


def _proposal(speaking, state, version):
    criteria = speaking.criteria(state)
    criteria[1]["points"] = "3"
    return speaking.action(
        version,
        "human-proposal",
        body={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": version["checksum"],
            "criteria": criteria,
        },
    )


def _appeal(speaking, state, *, decision="UPHOLD"):
    admin = speaking.login("admin@demo.prufai.local")
    appeal = speaking.call(
        "POST",
        f"/api/v1/candidate/results/{state['result']['result_version_id']}/appeals",
        state["headers"],
        json={
            "category": "SPEAKING",
            "grounds": "Original synthetic practice review requested.",
            "operation_id": str(uuid.uuid4()),
        },
    )
    path = speaking.org + f"/appeals/{appeal['id']}"
    speaking.call("POST", path + "/review", admin)
    detail = speaking.call("GET", path, admin)
    speaking.call(
        "POST",
        path + "/adjudicate",
        admin,
        json={
            "appeal_version_number": detail["current_version_number"],
            "appeal_checksum": detail["current_checksum"],
            "evidence_bundle_checksum": detail["evidence_bundle_checksum"],
            "decision": decision,
            "operation_id": str(uuid.uuid4()),
            "reason": "Independent review of this original technical-audio practice workflow.",
        },
    )
    return path, speaking.call("GET", path, admin)


def _correction_request(state, appeal):
    return {
        "appeal_version_number": appeal["current_version_number"],
        "appeal_checksum": appeal["current_checksum"],
        "evidence_bundle_checksum": appeal["evidence_bundle_checksum"],
        "correction_type": "SPEAKING_REASSESSMENT",
        "operation_id": str(uuid.uuid4()),
        "source_component_version_id": state["approved"]["version_id"],
        "source_component_checksum": state["approved"]["checksum"],
    }


def test_approved_speaking_produces_exact_score_release_statement_and_safe_audit(speaking):
    before_ai = speaking.session.scalar(select(func.count()).select_from(AIRun))
    state = _release(speaking, _human(speaking))
    speaking.session.expire_all()
    run = speaking.session.get(ScoringRun, uuid.UUID(state["run"]["id"]))
    result = speaking.session.get(ResultVersion, uuid.UUID(state["result"]["result_version_id"]))
    score = speaking.session.scalar(select(ItemScore).where(ItemScore.scoring_run_id == run.id))
    version = speaking.session.get(
        SubjectiveAssessmentVersion, uuid.UUID(state["approved"]["version_id"])
    )
    assert result.scoring_run_id == run.id and result.scoring_run_checksum == run.checksum
    assert result.raw_awarded_points == 4 and result.raw_maximum_points == 6
    assert result.outcome.value == "UNDETERMINED"
    assert score.awarded_points == Decimal("4") and score.maximum_points == Decimal("6")
    assert score.candidate_response_revision_id == state["revision"].id
    assert score.scoring_metadata["subjective_assessment_version_id"] == str(version.id)
    assert score.scoring_metadata["subjective_assessment_checksum"] == version.checksum
    encoded = json.dumps(score.scoring_metadata, default=str)
    assert state["asset"]["media_asset_id"] in encoded and state["asset"]["sha256"] in encoded
    component = state["result"]["assessment_components"][0]
    assert component["skill"] == "SPEAKING" and component["assessment_origin"] == "HUMAN"
    assert Decimal(component["approved_score"]) == 4 and Decimal(component["maximum_points"]) == 6
    assert component["subjective_assessment_version_id"] == str(version.id)
    candidate_statement = speaking.call(
        "GET",
        f"/api/v1/candidate/releases/{state['release']['release_id']}/statement",
        state["headers"],
    )
    assert candidate_statement == state["statement"]
    payload = candidate_statement["payload"]
    assert payload["classification"] == "PRACTICE"
    assert payload["scoring_run_id"] == str(run.id) and payload["result_version_id"] == str(
        result.id
    )
    assert payload["assessment_components"] == [component]
    assert payload["issued_at"] and payload["issued_by_user_id"]
    assert len(candidate_statement["checksum"]) == 64
    _public(candidate_statement, speaking, state)
    _public(state["result"], speaking, state)
    assert speaking.session.scalar(select(func.count()).select_from(AIRun)) == before_ai
    # HEAD and both authenticated stream routes use the same privately stored object.
    asset = speaking.asset(state["asset"])
    assert speaking.storage.exists(asset.storage_key)
    assert (
        hashlib.sha256(speaking.storage.get(asset.storage_key)).hexdigest() == version.audio_sha256
    )
    candidate = speaking.client.get(
        f"/api/v1/candidate/audio-assets/{asset.id}/stream", headers=state["headers"]
    )
    reviewer_path = (
        speaking.org
        + f"/candidate-attempts/{state['id']}/items/"
        + state["items"][0]["binding_id"]
        + f"/audio-assets/{asset.id}/stream"
    )
    reviewer = speaking.client.get(
        reviewer_path, headers=speaking.login("reviewer@demo.prufai.local")
    )
    assert candidate.status_code == reviewer.status_code == 200
    assert candidate.content == reviewer.content == technical_tone_wav()
    audit = speaking.call(
        "GET",
        "/api/v1/platform/control-center/audit",
        speaking.login(PLATFORM_ADMIN_EMAIL, tenant=False),
        params={"organisation_id": speaking.ids["organisation_id"], "limit": 100},
    )
    visible = [
        row
        for row in audit["items"]
        if row["entity_id"]
        in {
            str(version.id),
            str(run.id),
            str(result.id),
            state["release"]["release_id"],
            state["statement"]["id"],
            state["asset"]["media_asset_id"],
        }
    ]
    assert {"scoring.run.completed", "result.version.created", "statement.created"} <= {
        row["event_type"] for row in visible
    }
    _public(visible, speaking, state)
    refs = json.dumps(visible)
    assert state["asset"]["media_asset_id"] in refs and state["asset"]["sha256"] in refs


@pytest.mark.parametrize("stage", ["NONE", "REVIEW_REQUIRED", "IN_REVIEW"])
def test_unapproved_speaking_is_incomplete_and_cannot_be_reviewed_or_released(speaking, stage):
    state = speaking.submitted()
    if stage != "NONE":
        created = speaking.initialize(state)
        if stage == "IN_REVIEW":
            speaking.propose(state, created)
    _score(speaking, state)
    result = _result(speaking, state)
    assert result["status"] == "INCOMPLETE" and result["assessment_components"] == []
    path = speaking.org + f"/result-versions/{result['result_version_id']}"
    for action in ("request-review", "approve", "release"):
        options = {"json": {"operation_id": str(uuid.uuid4())}} if action == "release" else {}
        speaking.call(
            "POST",
            path + "/" + action,
            speaking.login("admin@demo.prufai.local"),
            expected=409,
            **options,
        )


@pytest.mark.parametrize("stage", ["REVIEW_REQUIRED", "IN_REVIEW", "REJECTED", "CHANGES_REQUESTED"])
def test_older_approval_never_silently_scores_through_an_unresolved_successor(speaking, stage):
    state = _human(speaking)
    first = _score(speaking, state)
    first_result = _result(speaking, state)
    successor = _successor(speaking, state)
    if stage != "REVIEW_REQUIRED":
        proposal = _proposal(speaking, state, successor)
        if stage != "IN_REVIEW":
            speaking.action(
                proposal,
                "human-decision",
                email="admin@demo.prufai.local",
                body={
                    "operation_id": str(uuid.uuid4()),
                    "expected_checksum": proposal["checksum"],
                    "decision": stage,
                    "reason": "Independent negative technical workflow decision.",
                },
            )
    _score(speaking, state, expected=409)
    unchanged = _result(speaking, state)
    assert unchanged["scoring_run_id"] == first["id"]
    assert unchanged["result_version_id"] == first_result["result_version_id"]
    speaking.session.expire_all()
    assert (
        speaking.session.scalar(
            select(func.count())
            .select_from(ScoringRun)
            .where(ScoringRun.attempt_id == uuid.UUID(state["id"]))
        )
        == 1
    )


def test_new_approved_successor_pins_only_its_new_score_and_preserves_prior_run(speaking):
    state = _human(speaking)
    first = _score(speaking, state)
    speaking.session.expire_all()
    prior = speaking.session.get(ScoringRun, uuid.UUID(first["id"]))
    checksum = prior.checksum
    snapshot = copy.deepcopy(prior.snapshot.snapshot_json)
    approved = speaking.approve(_proposal(speaking, state, _successor(speaking, state)))
    second = _score(speaking, state)
    assert second["id"] != first["id"] and _score(speaking, state) == second
    result = _result(speaking, state)
    assert Decimal(result["raw_awarded_points"]) == 5
    assert (
        result["assessment_components"][0]["subjective_assessment_version_id"]
        == approved["version_id"]
    )
    speaking.session.expire_all()
    assert speaking.session.get(ScoringRun, prior.id).checksum == checksum
    assert speaking.session.get(ScoringRun, prior.id).snapshot.snapshot_json == snapshot


def test_upheld_speaking_correction_requires_fresh_humans_and_preserves_released_history(speaking):
    state = _release(speaking, _human(speaking))
    speaking.session.expire_all()
    source = speaking.session.get(
        SubjectiveAssessmentVersion, uuid.UUID(state["approved"]["version_id"])
    )
    old_run = speaking.session.get(ScoringRun, uuid.UUID(state["run"]["id"]))
    old_result = speaking.session.get(
        ResultVersion, uuid.UUID(state["result"]["result_version_id"])
    )
    old_release = speaking.session.get(ResultRelease, uuid.UUID(state["release"]["release_id"]))
    old_statement = speaking.session.get(StatementOfResults, uuid.UUID(state["statement"]["id"]))
    frozen = {
        "source_checksum": source.checksum,
        "values": copy.deepcopy(source.human_approved_values),
        "run_checksum": old_run.checksum,
        "snapshot": copy.deepcopy(old_run.snapshot.snapshot_json),
        "result_checksum": old_result.checksum,
        "release_checksum": old_release.checksum,
        "statement_checksum": old_statement.checksum,
        "statement_payload": copy.deepcopy(old_statement.payload),
    }
    path, appeal = _appeal(speaking, state)
    body = _correction_request(state, appeal)
    admin = speaking.login("admin@demo.prufai.local")
    created = speaking.call("POST", path + "/correction", admin, json=body)
    assert created["status"] == "AWAITING_COMPONENT_REVIEW"
    assert created["new_scoring_run_id"] is created["new_result_version_id"] is None
    assert speaking.call("POST", path + "/correction", admin, json=body) == created
    altered = {**body, "source_component_checksum": "0" * 64}
    speaking.call("POST", path + "/correction", admin, expected=409, json=altered)
    speaking.call("POST", path + "/correction", state["headers"], expected=403, json=body)
    speaking.session.expire_all()
    component = speaking.session.get(
        SubjectiveAssessmentVersion, uuid.UUID(created["new_component_version_id"])
    )
    correction = speaking.session.get(ResultCorrection, uuid.UUID(created["id"]))
    assert component.status.value == "REVIEW_REQUIRED" and not component.criterion_assessments
    assert component.assessment_origin == "HUMAN"
    assert component.candidate_media_asset_id == source.candidate_media_asset_id
    assert component.audio_sha256 == source.audio_sha256
    assert component.candidate_response_revision_id == source.candidate_response_revision_id
    assert component.provenance["result_correction_id"] == str(correction.id)
    assert component.provenance["result_correction"]["source_result_version_id"] == str(
        old_result.id
    )
    resume_path = speaking.org + f"/corrections/{correction.id}/resume"
    speaking.call(
        "POST", resume_path, admin, expected=409, json={"component_checksum": component.checksum}
    )
    successor = {
        "id": str(component.subjective_assessment_id),
        "version_id": str(component.id),
        "checksum": component.checksum,
    }
    proposed = _proposal(speaking, state, successor)
    speaking.action(proposed, "human-approve", expected=403)
    approved = speaking.approve(proposed)
    speaking.call(
        "POST", resume_path, admin, expected=409, json={"component_checksum": successor["checksum"]}
    )
    rescored = speaking.call(
        "POST", resume_path, admin, json={"component_checksum": approved["checksum"]}
    )
    assert rescored["status"] == "RESCORED"
    assert (
        speaking.call("POST", resume_path, admin, json={"component_checksum": approved["checksum"]})
        == rescored
    )
    speaking.call("POST", resume_path, admin, expected=409, json={"component_checksum": "0" * 64})
    speaking.session.expire_all()
    new_result = speaking.session.get(ResultVersion, uuid.UUID(rescored["new_result_version_id"]))
    new_run = speaking.session.get(ScoringRun, uuid.UUID(rescored["new_scoring_run_id"]))
    assert new_run.id != old_run.id and new_result.id != old_result.id
    assert new_result.scoring_run_id == new_run.id
    assert new_result.supersedes_version_id == old_result.id
    assert new_result.status.value == "REVIEW_REQUIRED" and new_result.raw_awarded_points == 5
    score = speaking.session.scalar(select(ItemScore).where(ItemScore.scoring_run_id == new_run.id))
    assert score.scoring_metadata["subjective_assessment_version_id"] == str(component.id)
    assert score.scoring_metadata["subjective_assessment_checksum"] == approved["checksum"]
    assert (
        speaking.session.get(SubjectiveAssessmentVersion, source.id).checksum
        == frozen["source_checksum"]
    )
    assert (
        speaking.session.get(SubjectiveAssessmentVersion, source.id).human_approved_values
        == frozen["values"]
    )
    assert speaking.session.get(ScoringRun, old_run.id).checksum == frozen["run_checksum"]
    assert speaking.session.get(ScoringRun, old_run.id).snapshot.snapshot_json == frozen["snapshot"]
    assert speaking.session.get(ResultVersion, old_result.id).checksum == frozen["result_checksum"]
    assert speaking.session.get(ResultVersion, old_result.id).status.value == "RELEASED"
    assert (
        speaking.session.get(ResultRelease, old_release.id).checksum == frozen["release_checksum"]
    )
    assert speaking.session.get(ResultRelease, old_release.id).status.value == "RELEASED"
    assert (
        speaking.session.get(StatementOfResults, old_statement.id).checksum
        == frozen["statement_checksum"]
    )
    assert (
        speaking.session.get(StatementOfResults, old_statement.id).payload
        == frozen["statement_payload"]
    )
    assert speaking.session.scalar(select(func.count()).select_from(ResultRelease)) == 1
    historical = speaking.call(
        "GET", f"/api/v1/candidate/releases/{old_release.id}/statement", state["headers"]
    )
    assert (
        historical["payload"] == frozen["statement_payload"]
        and historical["effective_status"] == "BLOCKED"
    )
    _public(historical, speaking, state)
    new_path = speaking.org + f"/result-versions/{new_result.id}"
    speaking.call(
        "POST", new_path + "/release", admin, expected=409, json={"operation_id": str(uuid.uuid4())}
    )
    speaking.call("POST", new_path + "/request-review", admin)
    speaking.call("POST", new_path + "/approve", admin)
    released = speaking.call(
        "POST",
        new_path + "/release",
        admin,
        json={"operation_id": str(uuid.uuid4()), "supersedes_release_id": str(old_release.id)},
    )
    assert released["release_id"] != str(old_release.id)
    latest_statement = speaking.call(
        "POST",
        speaking.org + f"/releases/{released['release_id']}/statement",
        admin,
        json={"operation_id": str(uuid.uuid4())},
    )
    assert latest_statement["payload"]["scoring_run_id"] == str(new_run.id)
    assert latest_statement["payload"]["assessment_components"][0][
        "subjective_assessment_version_id"
    ] == str(component.id)
    speaking.session.expire_all()
    assert speaking.session.get(ResultVersion, old_result.id).status.value == "SUPERSEDED"
    assert (
        speaking.session.get(StatementOfResults, old_statement.id).payload
        == frozen["statement_payload"]
    )
    platform = speaking.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    audit = speaking.call(
        "GET",
        "/api/v1/platform/control-center/audit",
        platform,
        params={"organisation_id": speaking.ids["organisation_id"], "limit": 100},
    )
    rows = [row for row in audit["items"] if row["entity_id"] == str(correction.id)]
    assert rows and "result.correction_completed" in {row["event_type"] for row in rows}
    _public(rows, speaking, state)
    references = json.dumps(rows)
    for reference in (
        str(source.id),
        str(component.id),
        str(old_run.id),
        str(new_run.id),
        str(old_result.id),
        str(new_result.id),
    ):
        assert reference in references


@pytest.mark.parametrize(
    "invalid", ["rejected_appeal", "point_override", "missing_source", "wrong_source"]
)
def test_speaking_correction_cannot_bypass_the_upheld_exact_two_human_workflow(speaking, invalid):
    state = _release(speaking, _human(speaking))
    path, appeal = _appeal(
        speaking, state, decision="REJECT" if invalid == "rejected_appeal" else "UPHOLD"
    )
    body = _correction_request(state, appeal)
    if invalid == "point_override":
        body["corrections"] = {"LANGUAGE": {"approved_points": "3", "approved_level": "CLEAR"}}
    elif invalid == "missing_source":
        body.pop("source_component_version_id")
    elif invalid == "wrong_source":
        body["source_component_version_id"] = str(uuid.uuid4())
    speaking.call(
        "POST",
        path + "/correction",
        speaking.login("admin@demo.prufai.local"),
        expected=409,
        json=body,
    )
    speaking.session.expire_all()
    assert speaking.session.scalar(select(func.count()).select_from(ResultCorrection)) == 0
    assert (
        speaking.session.scalar(select(func.count()).select_from(SubjectiveAssessmentVersion)) == 1
    )


def test_candidates_foreign_members_and_platform_cannot_score_or_release_speaking(speaking):
    state = _release(speaking, _human(speaking))
    actors = [
        state["headers"],
        speaking.login(speaking.users[2].email, org=str(speaking.foreign_org)),
        speaking.login(PLATFORM_ADMIN_EMAIL),
    ]
    for actor in actors:
        _score(speaking, state, expected=403, headers=actor)
        speaking.call(
            "POST", state["release_path"], actor, expected=403, json=state["release_operation"]
        )
        speaking.call(
            "POST",
            speaking.org + f"/releases/{state['release']['release_id']}/revoke",
            actor,
            expected=403,
            json={"operation_id": str(uuid.uuid4()), "reason": "Unauthorized revocation attempt."},
        )
        speaking.session.expire_all()
        assert (
            speaking.session.get(
                ResultRelease, uuid.UUID(state["release"]["release_id"])
            ).status.value
            == "RELEASED"
        )
    second_candidate = speaking.login(speaking.users[1].email)
    for actor in (second_candidate, actors[1]):
        for path in (
            f"/api/v1/candidate/releases/{state['release']['release_id']}",
            f"/api/v1/candidate/releases/{state['release']['release_id']}/statement",
        ):
            response = speaking.client.get(path, headers=actor)
            assert response.status_code in {403, 404}, response.text
    forged = speaking.client.post(
        speaking.org + f"/exam-attempts/{state['id']}/score",
        headers=speaking.login("admin@demo.prufai.local"),
        json={"approved_score": "999", "raw_awarded_points": "999"},
    )
    assert forged.status_code in {200, 422}
    assert Decimal(_result(speaking, state)["raw_awarded_points"]) == 4
    speaking.call(
        "POST",
        speaking.org + f"/releases/{state['release']['release_id']}/revoke",
        speaking.login("admin@demo.prufai.local"),
        json={
            "operation_id": str(uuid.uuid4()),
            "reason": "Authorized original practice test revocation.",
        },
    )
    speaking.session.expire_all()
    assert (
        speaking.session.get(ResultRelease, uuid.UUID(state["release"]["release_id"])).status.value
        == "REVOKED"
    )


@pytest.mark.parametrize(
    "corruption",
    ["asset_sha", "incomplete_audio", "criterion_points", "rubric_checksum", "response_checksum"],
)
def test_scoring_revalidates_exact_approved_evidence_before_reusing_a_historical_run(
    speaking, corruption
):
    state = _human(speaking)
    first = _score(speaking, state)
    speaking.session.expire_all()
    version = speaking.session.get(
        SubjectiveAssessmentVersion, uuid.UUID(state["approved"]["version_id"])
    )
    asset = speaking.asset(state["asset"])
    # Deliberately hostile persisted-state fixtures use a separate raw connection
    # to bypass immutable ORM guards. No legitimate assessed outcome is inserted.
    with speaking.session.get_bind().begin() as connection:
        if corruption == "asset_sha":
            connection.execute(
                CandidateMediaAsset.__table__.update()
                .where(CandidateMediaAsset.id == asset.id)
                .values(sha256="0" * 64)
            )
        elif corruption == "incomplete_audio":
            proof = copy.deepcopy(asset.validation_provenance)
            proof.pop("complete_input")
            connection.execute(
                CandidateMediaAsset.__table__.update()
                .where(CandidateMediaAsset.id == asset.id)
                .values(validation_provenance=proof)
            )
        elif corruption == "criterion_points":
            connection.execute(
                SubjectiveCriterionAssessment.__table__.update()
                .where(SubjectiveCriterionAssessment.id == version.criterion_assessments[0].id)
                .values(approved_points=Decimal("3"))
            )
        elif corruption == "rubric_checksum":
            connection.execute(
                RubricVersion.__table__.update()
                .where(RubricVersion.id == version.rubric_version_id)
                .values(checksum="0" * 64)
            )
        else:
            connection.execute(
                CandidateResponseRevision.__table__.update()
                .where(CandidateResponseRevision.id == version.candidate_response_revision_id)
                .values(payload_checksum="0" * 64)
            )
    _score(speaking, state, expected=409)
    speaking.session.expire_all()
    assert speaking.session.get(ScoringRun, uuid.UUID(first["id"])) is not None
    assert (
        speaking.session.scalar(
            select(func.count())
            .select_from(ScoringRun)
            .where(ScoringRun.attempt_id == uuid.UUID(state["id"]))
        )
        == 1
    )


def test_ambiguous_active_approvals_fail_closed_in_the_real_domain_tables(speaking):
    state = _human(speaking)
    _score(speaking, state)
    speaking.approve(_proposal(speaking, state, _successor(speaking, state)))
    with speaking.session.get_bind().begin() as connection:
        connection.execute(
            SubjectiveAssessmentVersion.__table__.update()
            .where(SubjectiveAssessmentVersion.id == uuid.UUID(state["approved"]["version_id"]))
            .values(status="APPROVED")
        )
    _score(speaking, state, expected=409)


def test_created_human_record_is_not_an_approved_scoring_source(speaking):
    state = speaking.submitted()
    created = speaking.initialize(state)
    with speaking.session.get_bind().begin() as connection:
        connection.execute(
            SubjectiveAssessmentVersion.__table__.update()
            .where(SubjectiveAssessmentVersion.id == uuid.UUID(created["version_id"]))
            .values(status="CREATED")
        )
    _score(speaking, state)
    assert _result(speaking, state)["status"] == "INCOMPLETE"


@pytest.mark.parametrize("stage", ["review", "release", "statement"])
def test_new_result_actions_revalidate_current_speaking_authority_while_preserving_replays(
    speaking, stage
):
    state = _human(speaking)
    _score(speaking, state)
    result = _result(speaking, state)
    admin = speaking.login("admin@demo.prufai.local")
    path = speaking.org + f"/result-versions/{result['result_version_id']}"
    operation = {"operation_id": str(uuid.uuid4())}
    if stage in {"release", "statement"}:
        speaking.call("POST", path + "/request-review", admin)
        speaking.call("POST", path + "/approve", admin)
    if stage == "statement":
        released = speaking.call("POST", path + "/release", admin, json=operation)
    _successor(speaking, state)
    if stage == "review":
        for action in ("request-review", "approve"):
            speaking.call("POST", path + "/" + action, admin, expected=409)
    elif stage == "release":
        speaking.call("POST", path + "/release", admin, expected=409, json=operation)
    else:
        assert speaking.call("POST", path + "/release", admin, json=operation) == released
        speaking.call(
            "POST",
            speaking.org + f"/releases/{released['release_id']}/statement",
            admin,
            expected=409,
            json={"operation_id": str(uuid.uuid4())},
        )
        speaking.session.expire_all()
        assert speaking.session.scalar(select(func.count()).select_from(StatementOfResults)) == 0
