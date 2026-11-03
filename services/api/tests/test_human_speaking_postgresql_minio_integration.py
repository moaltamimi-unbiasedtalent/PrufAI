"""HUMAN_ONLY Speaking through real JWT, fresh Alembic PostgreSQL and private S3.

Original generated tones are genuine submitted technical audio, never fabricated
speech/transcripts or AI evidence. Scoring/results/release deliberately do not run.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Lock
from typing import Any

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url

from app.authorization import build_tenant_context
from app.models import (
    AIRun,
    CandidateAudioRecordingSession,
    CandidateMediaAsset,
    CandidateResponse,
    CandidateResponseMediaBinding,
    CandidateResponseRevision,
    HumanReviewRequest,
    RubricVersion,
    ScoringRun,
    SubjectiveAssessmentVersion,
    User,
    WritingGovernanceOperation,
)
from app.models.enums import AIRunStatus, CandidateMediaStatus
from app.scripts.seed_demo import PLATFORM_ADMIN_EMAIL
from app.services.organisations import OrganisationService
from tests.alembic_helpers import repository_alembic_head
from tests.integration_support.audio_evidence import technical_tone_wav
from tests.test_recorded_audio_postgresql_minio_integration import RecordedAudio
from tests.test_recorded_audio_postgresql_minio_integration import audio as recorded_audio_fixture
from tests.test_speaking_human_demo_seed import assert_speaking_human_seed_preconditions

REQUIRED = (
    os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
    or os.getenv("PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION") == "1"
)
pytestmark = pytest.mark.skipif(
    not (os.getenv("POSTGRES_INTEGRATION_URL") and os.getenv("OBJECT_STORAGE_ENDPOINT"))
    and not REQUIRED,
    reason="HUMAN Speaking requires real PostgreSQL AND private MinIO/S3",
)


class HumanSpeaking(RecordedAudio):
    def __repr__(self) -> str:
        return "<HumanSpeaking real PostgreSQL/private S3 fixture>"

    def start(self, index: int = 0) -> dict[str, Any]:
        assignment = self.call(
            "POST",
            self.org + "/exam-assignments",
            self.login("admin@demo.prufai.local"),
            expected=201,
            json={
                "candidate_user_id": str(self.users[index].id),
                "exam_version_id": self.ids["speaking_human_exam_version_id"],
            },
        )
        headers = self.login(self.users[index].email)
        attempt = self.call(
            "POST",
            f"/api/v1/candidate/exam-assignments/{assignment['id']}/attempts",
            headers,
            expected=201,
        )
        path = f"/api/v1/candidate/exam-attempts/{attempt['id']}"
        runtime = self.call("POST", path + "/start", headers)
        items = [
            item
            for module in runtime["runtime_plan"]["modules"]
            for section in module["sections"]
            for task in section["tasks"]
            for item in task["items"]
        ]
        assert len(items) == 1 and items[0]["response_mode"] == "AUDIO"
        assert items[0]["item_version_id"] == self.ids["speaking_human_item_version_id"]
        return {"id": attempt["id"], "path": path, "headers": headers, "items": items}

    @property
    def rubric_path(self) -> str:
        return (
            self.org
            + f"/rubrics/{self.ids['speaking_human_rubric_id']}/versions/"
            + self.ids["speaking_human_rubric_version_id"]
        )

    def rubric(self):
        self.session.expire_all()
        row = self.session.get(
            RubricVersion, uuid.UUID(self.ids["speaking_human_rubric_version_id"])
        )
        return {"id": str(row.id), "checksum": row.checksum}

    def publish(self):
        version = self.rubric()
        for action, email in (
            ("request-review", "admin@demo.prufai.local"),
            ("approve", "reviewer@demo.prufai.local"),
            ("publish", "reviewer@demo.prufai.local"),
        ):
            version = self.call(
                "POST",
                self.rubric_path + "/" + action,
                self.login(email),
                json={"operation_id": str(uuid.uuid4()), "expected_checksum": version["checksum"]},
            )
        return version

    def submitted(self, *, submit=True, publish=True):
        rubric = self.publish() if publish else self.rubric()
        state = self.start()
        asset = self.finalize(state, self.record(state))
        assert asset["status"] == "READY" and asset["duration_ms"] == 1000
        assert asset["validation_provenance"]["complete_input"] is True
        self.attach(state, asset)
        if submit:
            self.call("POST", state["path"] + "/submit", state["headers"])
        self.session.expire_all()
        revision = self.session.scalar(
            select(CandidateResponseRevision).where(
                CandidateResponseRevision.attempt_id == uuid.UUID(state["id"])
            )
        )
        assert revision is not None
        return {**state, "asset": asset, "revision": revision, "rubric": rubric}

    def initialize_body(self, state):
        return {
            "operation_id": str(uuid.uuid4()),
            "attempt_id": state["id"],
            "exam_version_item_binding_id": state["items"][0]["binding_id"],
            "candidate_response_revision_id": str(state["revision"].id),
            "response_checksum": state["revision"].payload_checksum,
            "rubric_version_id": state["rubric"]["id"],
            "rubric_checksum": state["rubric"]["checksum"],
        }

    def initialize(self, state, *, body=None, headers=None, expected=200):
        return self.call(
            "POST",
            self.org + "/subjective-assessments/human-speaking",
            headers or self.login("reviewer@demo.prufai.local"),
            expected=expected,
            json=body or self.initialize_body(state),
        )

    def criteria(self, state):
        reference = {
            "asset_id": state["asset"]["media_asset_id"],
            "audio_sha256": state["asset"]["sha256"],
            "candidate_response_revision_id": str(state["revision"].id),
            "start_ms": 0,
            "end_ms": 1000,
            "time_unit": "milliseconds",
        }
        return [
            {
                "criterion_code": code,
                "points": "2",
                "level": "CLEAR",
                "rationale": "Private HUMAN technical audio acceptance rationale.",
                "evidence": [copy.deepcopy(reference)],
            }
            for code in ("TASK", "LANGUAGE")
        ]

    def action(
        self,
        created,
        action,
        *,
        body=None,
        email="reviewer@demo.prufai.local",
        expected=200,
        headers=None,
    ):
        return self.call(
            "POST",
            self.org + f"/speaking-reviews/{created['version_id']}/{action}",
            headers or self.login(email),
            expected=expected,
            json=body
            or {"operation_id": str(uuid.uuid4()), "expected_checksum": created["checksum"]},
        )

    def propose(self, state, created):
        return self.action(
            created,
            "human-proposal",
            body={
                "operation_id": str(uuid.uuid4()),
                "expected_checksum": created["checksum"],
                "criteria": self.criteria(state),
            },
        )

    def approve(self, proposed):
        return self.action(proposed, "human-approve", email="admin@demo.prufai.local")


@pytest.fixture
def speaking(monkeypatch):
    """Only create/drop this fixture's own unique database; never reset the supplied DB."""
    configured = os.getenv("POSTGRES_INTEGRATION_URL")
    if not configured:
        pytest.fail("Required HUMAN Speaking gate needs POSTGRES_INTEGRATION_URL")
    source = make_url(configured)
    database = "prufai_human_speaking_" + uuid.uuid4().hex
    fresh = source.set(database=database).render_as_string(hide_password=False)
    control = source.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(control, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    generator = None
    try:
        environment = {**os.environ, "DATABASE_URL": fresh}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr
        monkeypatch.setenv("POSTGRES_INTEGRATION_URL", fresh)
        generator = recorded_audio_fixture.__wrapped__()
        audio = next(generator)
        yield HumanSpeaking(**audio.__dict__)
    finally:
        if generator is not None:
            generator.close()
        with psycopg.connect(control, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


def test_human_speaking_starts_from_current_migrations_and_unassessed_original_seed(speaking):
    assert (
        speaking.session.scalar(text("SELECT version_num FROM alembic_version"))
        == repository_alembic_head()
    )
    assert_speaking_human_seed_preconditions(speaking.session)
    assert (
        speaking.storage.client.head_bucket(Bucket=speaking.storage.bucket)["ResponseMetadata"][
            "HTTPStatusCode"
        ]
        == 200
    )


def test_published_rubric_real_submitted_audio_independent_human_approval_and_safe_audit(
    speaking, monkeypatch
):
    before_ai = speaking.session.scalar(select(func.count()).select_from(AIRun))
    before_scoring = speaking.session.scalar(select(func.count()).select_from(ScoringRun))
    state = speaking.submitted()
    created = speaking.initialize(state)
    proposed = speaking.propose(state, created)
    assert proposed["status"] == "IN_REVIEW"
    speaking.action(proposed, "human-approve", expected=403)
    approved = speaking.approve(proposed)
    assert approved["status"] == "APPROVED"
    speaking.session.expire_all()
    version = speaking.session.get(SubjectiveAssessmentVersion, uuid.UUID(created["version_id"]))
    assert version.assessment_origin == "HUMAN"
    assert (
        speaking.session.get(RubricVersion, version.rubric_version_id).skill.value == "SPEAKING"
        and version.approved_score == 4
    )
    assert version.candidate_response_revision_id == state["revision"].id
    assert str(version.candidate_media_asset_id) == state["asset"]["media_asset_id"]
    assert version.audio_sha256 == state["asset"]["sha256"]
    assert version.rubric_checksum == state["rubric"]["checksum"]
    assert version.speech_transcription_version_id is None and version.transcript_checksum is None
    assert version.ai_run_id is version.model_profile_id is version.prompt_version_id is None
    assert version.ai_proposal == {} and all(
        row.confidence is None for row in version.criterion_assessments
    )
    from app.speaking_assessment.governance import HumanSpeakingService

    HumanSpeakingService.validate_approved_evidence(speaking.session, version)
    assert speaking.session.scalar(select(func.count()).select_from(AIRun)) == before_ai
    assert speaking.session.scalar(select(func.count()).select_from(ScoringRun)) == before_scoring
    # A 206 must call private backend Range retrieval; fail if the endpoint reads
    # a full object before slicing. The underlying client is the actual S3 client.
    ranges = []
    original = speaking.storage.read_range

    def bounded(key, start, end):
        ranges.append((start, end))
        return original(key, start, end)

    monkeypatch.setattr(
        type(speaking.storage), "read_range", lambda self, key, start, end: bounded(key, start, end)
    )
    monkeypatch.setattr(
        type(speaking.storage),
        "get",
        lambda *_a, **_k: pytest.fail("206 performed full-object GET"),
    )
    url = (
        speaking.org
        + f"/candidate-attempts/{state['id']}/items/{state['items'][0]['binding_id']}"
        + f"/audio-assets/{state['asset']['media_asset_id']}/stream"
    )
    reviewer = speaking.login("reviewer@demo.prufai.local")
    raw = technical_tone_wav()
    for header, expected_bytes in (
        ("bytes=10-29", raw[10:30]),
        ("bytes=40-", raw[40:]),
        ("bytes=-20", raw[-20:]),
    ):
        response = speaking.client.get(url, headers={**reviewer, "Range": header})
        assert response.status_code == 206 and response.content == expected_bytes, response.text
    assert len(ranges) == 3
    platform = speaking.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    page = speaking.call(
        "GET",
        "/api/v1/platform/control-center/audit",
        platform,
        params={"organisation_id": speaking.ids["organisation_id"], "limit": 100},
    )
    visible = [
        row
        for row in page["items"]
        if row.get("entity_id") in {str(version.id), state["rubric"]["id"]}
    ]
    assert visible and any(
        row["event_type"] == "subjective_assessment.human_approved" for row in visible
    )
    rendered = json.dumps(visible)
    asset = speaking.asset(state["asset"])
    for private in (
        asset.storage_key,
        "Private HUMAN technical audio acceptance rationale.",
        "candidate speech",
        "access_token",
    ):
        assert private not in rendered


def test_rubric_requires_independent_governance_and_cannot_be_rewritten(speaking):
    initial = speaking.rubric()
    admin = speaking.login("admin@demo.prufai.local")
    requested = speaking.call(
        "POST",
        speaking.rubric_path + "/request-review",
        admin,
        json={"operation_id": str(uuid.uuid4()), "expected_checksum": initial["checksum"]},
    )
    speaking.call(
        "POST",
        speaking.rubric_path + "/approve",
        admin,
        expected=403,
        json={"operation_id": str(uuid.uuid4()), "expected_checksum": requested["checksum"]},
    )
    for action in ("approve", "publish"):
        requested = speaking.call(
            "POST",
            speaking.rubric_path + "/" + action,
            speaking.login("reviewer@demo.prufai.local"),
            json={"operation_id": str(uuid.uuid4()), "expected_checksum": requested["checksum"]},
        )
    version = speaking.session.get(RubricVersion, uuid.UUID(initial["id"]))
    with pytest.raises(ValueError, match="immutable"):
        version.criteria[0]["max_points"] = "999"
        speaking.session.commit()
    speaking.session.rollback()


def test_initializer_denies_unsubmitted_unpublished_stale_and_arbitrary_evidence(speaking):
    state = speaking.submitted(submit=False, publish=False)
    speaking.initialize(state, expected=409)
    speaking.call("POST", state["path"] + "/submit", state["headers"])
    speaking.initialize(state, expected=409)
    state["rubric"] = speaking.publish()
    body = speaking.initialize_body(state)
    for name in (
        "candidate_response_revision_id",
        "rubric_version_id",
        "exam_version_item_binding_id",
    ):
        changed = {**body, "operation_id": str(uuid.uuid4()), name: str(uuid.uuid4())}
        speaking.initialize(state, body=changed, expected=409)
    for name in ("response_checksum", "rubric_checksum"):
        speaking.initialize(
            state, body={**body, "operation_id": str(uuid.uuid4()), name: "f" * 64}, expected=409
        )
    # Request models reject arbitrary evidence identifiers instead of trusting them.
    supplied = {**body, "candidate_media_asset_id": state["asset"]["media_asset_id"]}
    response = speaking.client.post(
        speaking.org + "/subjective-assessments/human-speaking",
        headers=speaking.login("reviewer@demo.prufai.local"),
        json=supplied,
    )
    assert response.status_code == 422, response.text
    assert speaking.initialize(state)["status"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize(
    "corruption",
    [
        "unready",
        "incomplete",
        "sha",
        "policy",
        "wrong_item_version",
        "response_pointer",
        "unattached",
    ],
)
def test_initializer_fails_closed_on_corrupt_or_legacy_audio_provenance(speaking, corruption):
    state = speaking.submitted()
    asset = speaking.asset(state["asset"])
    table = CandidateMediaAsset.__table__
    updates = {
        "unready": {"status": CandidateMediaStatus.VALIDATING},
        "incomplete": {
            "validation_provenance": {**asset.validation_provenance, "complete_input": False}
        },
        "sha": {"sha256": "f" * 64},
        "policy": {
            "evidence_identity": {
                **asset.recording_session.evidence_identity,
                "policy_checksum": "f" * 64,
            }
        },
        "wrong_item_version": {
            "evidence_identity": {
                **asset.recording_session.evidence_identity,
                "item_version_id": speaking.ids["audio_item_1_version_id"],
            }
        },
    }
    updates["response_pointer"] = {"current_revision": 0}
    row_id = asset.id
    if corruption == "response_pointer":
        table = CandidateResponse.__table__
        row_id = state["revision"].candidate_response_id
    if corruption in {"policy", "wrong_item_version"}:
        table = CandidateAudioRecordingSession.__table__
        row_id = asset.recording_session_id
    # Deliberate raw legacy/corruption fixture in this test's disposable DB.
    # A separate connection avoids asking the immutable ORM to authorize it.
    speaking.session.rollback()
    with speaking.session.get_bind().begin() as connection:
        if corruption == "unattached":
            connection.execute(
                CandidateResponseMediaBinding.__table__.delete().where(
                    CandidateResponseMediaBinding.__table__.c.candidate_media_asset_id == row_id
                )
            )
        else:
            connection.execute(
                table.update().where(table.c.id == row_id).values(**updates[corruption])
            )
    speaking.session.expire_all()
    speaking.initialize(state, expected=409)
    assert (
        speaking.session.scalar(select(func.count()).select_from(SubjectiveAssessmentVersion)) == 0
    )


def test_human_proposal_requires_exact_criterion_coverage_and_bounded_audio_spans(speaking):
    state = speaking.submitted()
    created = speaking.initialize(state)
    valid = speaking.criteria(state)
    invalid = []
    for start, end, unit in (
        (-1, 500, "milliseconds"),
        (500, 500, "milliseconds"),
        (501, 500, "milliseconds"),
        (0, 1001, "milliseconds"),
        (0, 1, "seconds"),
        (0.5, 800, "milliseconds"),
        (True, 800, "milliseconds"),
    ):
        rows = copy.deepcopy(valid)
        rows[0]["evidence"][0].update(start_ms=start, end_ms=end, time_unit=unit)
        invalid.append(rows)
    for key, value in (
        ("asset_id", str(uuid.uuid4())),
        ("audio_sha256", "f" * 64),
        ("candidate_response_revision_id", str(uuid.uuid4())),
    ):
        rows = copy.deepcopy(valid)
        rows[0]["evidence"][0][key] = value
        invalid.append(rows)
    for key, value in (
        ("points", "4"),
        ("points", "-1"),
        ("points", "NaN"),
        ("level", "INVENTED"),
        ("rationale", ""),
        ("evidence", []),
    ):
        rows = copy.deepcopy(valid)
        rows[0][key] = value
        invalid.append(rows)
    invalid.extend(
        (valid[:1], [valid[0], valid[0]], valid + [{**valid[0], "criterion_code": "OTHER"}])
    )
    for rows in invalid:
        speaking.action(
            created,
            "human-proposal",
            expected=409,
            body={
                "operation_id": str(uuid.uuid4()),
                "expected_checksum": created["checksum"],
                "criteria": rows,
            },
        )
    assert speaking.propose(state, created)["status"] == "IN_REVIEW"


def test_candidate_foreign_reviewer_and_platform_identity_cannot_operate_human_review(speaking):
    state = speaking.submitted()
    created = speaking.initialize(state)
    denied = [
        state["headers"],
        speaking.login(speaking.users[2].email, org=str(speaking.foreign_org)),
        speaking.login(PLATFORM_ADMIN_EMAIL),
    ]
    for headers in denied:
        speaking.initialize(state, headers=headers, expected=403)
        speaking.action(
            created,
            "human-proposal",
            headers=headers,
            expected=403,
            body={
                "operation_id": str(uuid.uuid4()),
                "expected_checksum": created["checksum"],
                "criteria": speaking.criteria(state),
            },
        )
        response = speaking.client.get(
            speaking.org + f"/speaking-reviews/{created['id']}", headers=headers
        )
        assert response.status_code == 403, response.text
        for route in ("/speaking-review-options", "/speaking-reviews"):
            response = speaking.client.get(speaking.org + route, headers=headers)
            assert response.status_code == 403, response.text
        speaking.call(
            "POST",
            speaking.rubric_path + "/publish",
            headers,
            expected=403,
            json={
                "operation_id": str(uuid.uuid4()),
                "expected_checksum": state["rubric"]["checksum"],
            },
        )
        private_route = (
            speaking.org
            + f"/candidate-attempts/{state['id']}/items/"
            + state["items"][0]["binding_id"]
            + f"/audio-assets/{state['asset']['media_asset_id']}/stream"
        )
        response = speaking.client.get(private_route, headers=headers)
        assert response.status_code == 403, response.text
        response = speaking.client.get(
            "/api/v1/candidate/audio-assets/" + state["asset"]["media_asset_id"] + "/stream",
            headers=headers,
        )
        # Own candidate audio remains permitted according to the evidence policy.
        assert response.status_code == (200 if headers == state["headers"] else 404), response.text
    other = speaking.login(speaking.users[1].email)
    assert (
        speaking.client.get(
            "/api/v1/candidate/audio-assets/" + state["asset"]["media_asset_id"] + "/stream",
            headers=other,
        ).status_code
        == 404
    )


def test_durable_replay_checks_payload_actor_tenant_and_current_authority(speaking):
    state = speaking.submitted()
    body = speaking.initialize_body(state)
    reviewer = speaking.login("reviewer@demo.prufai.local")
    created = speaking.initialize(state, body=body, headers=reviewer)
    assert speaking.initialize(state, body=body, headers=reviewer) == created
    speaking.initialize(
        state, body={**body, "response_checksum": "f" * 64}, headers=reviewer, expected=409
    )
    speaking.initialize(
        state, body=body, headers=speaking.login("admin@demo.prufai.local"), expected=409
    )
    foreign = speaking.login(speaking.users[2].email, org=str(speaking.foreign_org))
    speaking.initialize(state, body=body, headers=foreign, expected=403)
    proposal = {
        "operation_id": str(uuid.uuid4()),
        "expected_checksum": created["checksum"],
        "criteria": speaking.criteria(state),
    }
    first = speaking.action(created, "human-proposal", body=proposal)
    assert speaking.action(created, "human-proposal", body=proposal) == first
    changed = copy.deepcopy(proposal)
    changed["criteria"][0]["points"] = "1"
    speaking.action(created, "human-proposal", body=changed, expected=409)


def test_generic_ai_decision_and_shadow_review_cannot_approve_human_speaking(speaking):
    state = speaking.submitted()
    created = speaking.initialize(state)
    proposed = speaking.propose(state, created)
    review = speaking.session.scalar(
        select(HumanReviewRequest).where(
            HumanReviewRequest.entity_id == uuid.UUID(created["version_id"])
        )
    )
    assert review is not None and review.status.value == "PENDING"
    for action in ("approve", "reject", "request-changes", "decision"):
        kwargs = {"json": {"decision": "APPROVED"}} if action == "decision" else {}
        response = speaking.client.post(
            f"/api/v1/platform/ai/reviews/{review.id}/{action}",
            headers=speaking.login(PLATFORM_ADMIN_EMAIL, tenant=False),
            **kwargs,
        )
        assert response.status_code == 409, response.text
    platform = speaking.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    # An unexecuted attack envelope exercises the actual generic request endpoint.
    # It has no model/workflow/output and is not assessment or AI execution evidence.
    attack_run = AIRun(
        status=AIRunStatus.RUNNING, request_id="unexecuted-shadow-attack-" + uuid.uuid4().hex
    )
    speaking.session.add(attack_run)
    speaking.session.commit()
    shadows = []
    for alias in (
        "SUBJECTIVE_ASSESSMENT_VERSION",
        "SubjectiveAssessmentVersion",
        "untrusted-label",
    ):
        shadow = HumanReviewRequest(
            review_type="AI_PROPOSAL",
            entity_type=alias,
            entity_id=review.entity_id,
            status=review.status,
            requested_by=review.requested_by,
            requested_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            reviewed_checksum=review.reviewed_checksum,
        )
        speaking.session.add(shadow)
        speaking.session.commit()
        shadows.append(shadow.id)
        for action in ("approve", "reject", "request-changes", "decision"):
            kwargs = {"json": {"decision": "APPROVED"}} if action == "decision" else {}
            response = speaking.client.post(
                f"/api/v1/platform/ai/reviews/{shadow.id}/{action}", headers=platform, **kwargs
            )
            assert response.status_code == 409, response.text
        for target in (created["version_id"], created["id"], state["rubric"]["id"]):
            before = speaking.session.scalar(select(func.count()).select_from(HumanReviewRequest))
            response = speaking.client.post(
                f"/api/v1/platform/ai/runs/{attack_run.id}/review",
                headers=platform,
                json={"entity_type": alias, "entity_id": target, "checksum": proposed["checksum"]},
            )
            assert response.status_code == 409, response.text
            speaking.session.expire_all()
            assert speaking.session.get(AIRun, attack_run.id).status is AIRunStatus.RUNNING
            assert (
                speaking.session.scalar(select(func.count()).select_from(HumanReviewRequest))
                == before
            )
            assert (
                speaking.session.get(
                    SubjectiveAssessmentVersion, uuid.UUID(created["version_id"])
                ).ai_run_id
                is None
            )
    speaking.approve(proposed)
    speaking.session.expire_all()
    for shadow_id in shadows:
        stored = speaking.session.get(HumanReviewRequest, shadow_id)
        assert stored.status.value == "PENDING"
        assert stored.decided_by is stored.decided_at is stored.decision is None
    assert speaking.session.get(AIRun, attack_run.id).status is AIRunStatus.RUNNING


@pytest.mark.parametrize("decision", ["REJECTED", "CHANGES_REQUESTED"])
def test_independent_negative_decisions_require_reason_and_stale_decisions_fail(speaking, decision):
    state = speaking.submitted()
    proposed = speaking.propose(state, speaking.initialize(state))
    speaking.action(
        proposed,
        "human-decision",
        email="admin@demo.prufai.local",
        expected=409,
        body={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": "f" * 64,
            "decision": decision,
            "reason": "Private human reason",
        },
    )
    decided = speaking.action(
        proposed,
        "human-decision",
        email="admin@demo.prufai.local",
        body={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": proposed["checksum"],
            "decision": decision,
            "reason": "Private human reason",
        },
    )
    assert decided["status"] == decision
    speaking.action(proposed, "human-approve", email="admin@demo.prufai.local", expected=409)


def test_approved_version_nested_history_and_successor_remain_immutable(speaking):
    state = speaking.submitted()
    created = speaking.initialize(state)
    approved = speaking.approve(speaking.propose(state, created))
    version = speaking.session.get(SubjectiveAssessmentVersion, uuid.UUID(created["version_id"]))
    prior = (version.checksum, copy.deepcopy(version.human_approved_values), version.audio_sha256)
    changes = [
        lambda row: setattr(row, "approved_score", 99),
        lambda row: row.human_approved_values.update({"TASK": {"points": "99"}}),
        lambda row: row.criterion_assessments[0].evidence[0].update(end_ms=999),
        lambda row: setattr(row.criterion_assessments[0], "rationale", "rewritten"),
        lambda row: row.evidence_mode_summary.update({"TASK": "TRANSCRIPT"}),
    ]
    for change in changes:
        with pytest.raises(ValueError, match="immutable"):
            change(version)
            speaking.session.commit()
        speaking.session.rollback()
        speaking.session.expire_all()
        version = speaking.session.get(SubjectiveAssessmentVersion, version.id)
    successor = speaking.action(
        approved,
        "human-successor",
        email="admin@demo.prufai.local",
        body={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": approved["checksum"],
            "reason": "Fresh independent practice reassessment",
        },
    )
    assert (
        successor["version_id"] != approved["version_id"]
        and successor["status"] == "REVIEW_REQUIRED"
    )
    speaking.session.expire_all()
    assert speaking.session.get(SubjectiveAssessmentVersion, version.id).status.value == "APPROVED"
    successor_proposal = speaking.propose(state, successor)
    speaking.action(successor_proposal, "human-approve", expected=403)
    successor_approved = speaking.approve(successor_proposal)
    assert successor_approved["status"] == "APPROVED"
    speaking.session.expire_all()
    original = speaking.session.get(SubjectiveAssessmentVersion, version.id)
    assert original.status.value == "SUPERSEDED"
    assert (original.checksum, original.human_approved_values, original.audio_sha256) == prior
    assert len(original.assessment.versions) == 2


def _concurrent(speaking, target, bodies, headers):
    barrier = Barrier(2, timeout=10)
    guard = Lock()
    pids = set()
    engine = speaking.session.get_bind()

    def overlap(connection, _cursor, statement, _parameters, _context, _executemany):
        if target not in statement or "FOR UPDATE" not in statement:
            return
        pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with guard:
            if pid in pids:
                return
            pids.add(pid)
        barrier.wait()

    event.listen(engine, "before_cursor_execute", overlap)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(speaking.client.post, path, headers=actor, json=body)
                for (path, body), actor in zip(bodies, headers)
            ]
            responses = [future.result(timeout=30) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", overlap)
    assert len(pids) == 2
    return responses


def test_real_two_connection_initialization_replay_creates_one_assessment(speaking):
    state = speaking.submitted()
    body = speaking.initialize_body(state)
    path = speaking.org + "/subjective-assessments/human-speaking"
    responses = _concurrent(
        speaking,
        "candidate_exam_attempts",
        [(path, body)] * 2,
        [speaking.login("reviewer@demo.prufai.local") for _ in range(2)],
    )
    assert [row.status_code for row in responses] == [200, 200], [row.text for row in responses]
    assert responses[0].json() == responses[1].json()
    speaking.session.expire_all()
    assert (
        speaking.session.scalar(select(func.count()).select_from(SubjectiveAssessmentVersion)) == 1
    )
    assert (
        speaking.session.scalar(
            select(func.count())
            .select_from(WritingGovernanceOperation)
            .where(WritingGovernanceOperation.operation_id == uuid.UUID(body["operation_id"]))
        )
        == 1
    )


def test_real_two_connection_competing_approval_has_one_authoritative_decision(speaking):
    state = speaking.submitted()
    proposed = speaking.propose(state, speaking.initialize(state))
    admin = speaking.session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
    context = build_tenant_context(
        speaking.session, admin.id, uuid.UUID(speaking.ids["organisation_id"])
    )
    membership = speaking.users[1].memberships[0]
    OrganisationService.assign_membership_role(
        speaking.session, context, membership_id=membership.id, role_key="EXAM_MANAGER"
    )
    speaking.session.commit()
    path = speaking.org + f"/speaking-reviews/{proposed['version_id']}/human-approve"
    bodies = [
        (path, {"operation_id": str(uuid.uuid4()), "expected_checksum": proposed["checksum"]})
        for _ in range(2)
    ]
    responses = _concurrent(
        speaking,
        "subjective_assessment_versions",
        bodies,
        [speaking.login("admin@demo.prufai.local"), speaking.login(speaking.users[1].email)],
    )
    assert sorted(row.status_code for row in responses) == [200, 409], [
        row.text for row in responses
    ]
    speaking.session.expire_all()
    assert (
        speaking.session.scalar(
            select(func.count())
            .select_from(SubjectiveAssessmentVersion)
            .where(SubjectiveAssessmentVersion.status == "APPROVED")
        )
        == 1
    )
    reviews = speaking.session.scalars(
        select(HumanReviewRequest).where(
            HumanReviewRequest.entity_id == uuid.UUID(proposed["version_id"])
        )
    ).all()
    assert len(reviews) == 1 and reviews[0].status.value == "APPROVED"


def test_speaking_rubric_cannot_publish_a_transcript_or_ai_policy_as_human_audio(speaking):
    from app.scripts.seed_speaking_human_demo import (
        SPEAKING_HUMAN_CRITERIA,
        SPEAKING_HUMAN_LEVELS,
        speaking_human_id,
    )

    admin = speaking.login("admin@demo.prufai.local")
    root = speaking.call(
        "POST",
        speaking.org + "/rubrics",
        admin,
        json={"code": "SPEAKING_NEGATIVE_POLICY", "name": "Synthetic invalid policies"},
    )
    for index, mode in enumerate(("TRANSCRIPT", "AUDIO_AND_TRANSCRIPT", "AI_ASSISTED")):
        ai_policy = {"enabled": False, "mode": "HUMAN_ONLY"}
        evidence_mode = mode
        if mode == "AI_ASSISTED":
            ai_policy = {"enabled": True, "mode": "AI_ASSISTED"}
            evidence_mode = "AUDIO"
        version = speaking.call(
            "POST",
            speaking.org + f"/rubrics/{root['id']}/versions",
            admin,
            json={
                "standard_pack_version_id": speaking.ids["speaking_human_pack_version_id"],
                "task_spec_id": speaking.ids["speaking_human_task_spec_id"],
                "version_label": "invalid-policy-" + str(index),
                "skill": "SPEAKING",
                "criteria": SPEAKING_HUMAN_CRITERIA,
                "performance_levels": SPEAKING_HUMAN_LEVELS,
                "aggregation_policy": {"method": "SUM"},
                "evidence_policy": {
                    "mode": evidence_mode,
                    "required": True,
                    "minimum_references": 1,
                    "time_unit": "milliseconds",
                },
                "ai_assessment_policy": ai_policy,
                "human_review_policy": {"required": True, "separation_of_duties": True},
                "evidence_bindings": [
                    {
                        "knowledge_document_version_id": str(speaking_human_id("policy-document")),
                        "purpose": "ORIGINAL_PRACTICE_POLICY",
                    }
                ],
            },
        )
        if mode == "AI_ASSISTED":
            # Legacy AI configurations retain their governed AI lifecycle, but
            # assert_human_mode must never silently reinterpret one as HUMAN.
            from app.speaking_assessment.governance import HumanSpeakingService
            from app.writing_assessment.service import RubricError

            stored = speaking.session.get(RubricVersion, uuid.UUID(version["id"]))
            with pytest.raises(RubricError, match="HUMAN_ONLY"):
                HumanSpeakingService.assert_human_mode(stored)
        else:
            speaking.call(
                "POST",
                speaking.org + f"/rubrics/{root['id']}/versions/{version['id']}/request-review",
                admin,
                expected=409,
                json={"operation_id": str(uuid.uuid4()), "expected_checksum": version["checksum"]},
            )


def test_cached_reviewer_context_and_jwt_lose_authority_after_membership_suspension(speaking):
    from app.models import OrganisationMembership
    from app.models.enums import MembershipStatus
    from app.speaking_assessment.governance import HumanSpeakingService

    state = speaking.submitted()
    created = speaking.initialize(state)
    user = speaking.session.scalar(select(User).where(User.email == "reviewer@demo.prufai.local"))
    context = build_tenant_context(
        speaking.session, user.id, uuid.UUID(speaking.ids["organisation_id"])
    )
    headers = speaking.login(user.email)
    membership = speaking.session.scalar(
        select(OrganisationMembership).where(
            OrganisationMembership.user_id == user.id,
            OrganisationMembership.organisation_id == context.organisation_id,
        )
    )
    member_id = membership.id
    speaking.session.rollback()
    with speaking.session.get_bind().begin() as connection:
        connection.execute(
            OrganisationMembership.__table__.update()
            .where(OrganisationMembership.__table__.c.id == member_id)
            .values(status=MembershipStatus.SUSPENDED)
        )
    speaking.session.expire_all()
    with pytest.raises(PermissionError):
        HumanSpeakingService.propose(
            speaking.session,
            context,
            uuid.UUID(created["version_id"]),
            operation_id=uuid.uuid4(),
            expected_checksum=created["checksum"],
            criteria=speaking.criteria(state),
        )
    speaking.action(
        created,
        "human-proposal",
        headers=headers,
        expected=401,
        body={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": created["checksum"],
            "criteria": speaking.criteria(state),
        },
    )


def test_rejected_successor_chain_supersedes_prior_authoritative_approval_once(speaking):
    state = speaking.submitted()
    first = speaking.approve(speaking.propose(state, speaking.initialize(state)))
    first_row = speaking.session.get(SubjectiveAssessmentVersion, uuid.UUID(first["version_id"]))
    original = (first_row.checksum, copy.deepcopy(first_row.human_approved_values))
    second = speaking.action(
        first,
        "human-successor",
        email="admin@demo.prufai.local",
        body={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": first["checksum"],
            "reason": "Original synthetic reassessment requiring correction",
        },
    )
    second = speaking.propose(state, second)
    second = speaking.action(
        second,
        "human-decision",
        email="admin@demo.prufai.local",
        body={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": second["checksum"],
            "decision": "REJECTED",
            "reason": "Independent reviewer rejects this proposal",
        },
    )
    second_row = speaking.session.get(SubjectiveAssessmentVersion, uuid.UUID(second["version_id"]))
    rejected_history = (
        second_row.checksum,
        copy.deepcopy(second_row.provenance),
        copy.deepcopy(second_row.criterion_assessments[0].evidence),
    )
    third = speaking.action(
        second,
        "human-successor",
        email="admin@demo.prufai.local",
        body={
            "operation_id": str(uuid.uuid4()),
            "expected_checksum": second["checksum"],
            "reason": "Fresh independent proposal after rejected reassessment",
        },
    )
    third = speaking.approve(speaking.propose(state, third))
    speaking.session.expire_all()
    first_row = speaking.session.get(SubjectiveAssessmentVersion, first_row.id)
    second_row = speaking.session.get(SubjectiveAssessmentVersion, second_row.id)
    third_row = speaking.session.get(SubjectiveAssessmentVersion, uuid.UUID(third["version_id"]))
    assert first_row.status.value == "SUPERSEDED"
    assert (first_row.checksum, first_row.human_approved_values) == original
    assert second_row.status.value == "REJECTED"
    assert (
        second_row.checksum,
        second_row.provenance,
        second_row.criterion_assessments[0].evidence,
    ) == rejected_history
    assert third_row.status.value == "APPROVED"
    assert (
        speaking.session.scalar(
            select(func.count())
            .select_from(SubjectiveAssessmentVersion)
            .where(
                SubjectiveAssessmentVersion.subjective_assessment_id
                == first_row.subjective_assessment_id,
                SubjectiveAssessmentVersion.status == "APPROVED",
            )
        )
        == 1
    )
    from app.speaking_assessment.governance import HumanSpeakingService

    HumanSpeakingService.validate_approved_evidence(speaking.session, third_row)
    page = speaking.call(
        "GET",
        "/api/v1/platform/control-center/audit",
        speaking.login(PLATFORM_ADMIN_EMAIL, tenant=False),
        params={
            "organisation_id": speaking.ids["organisation_id"],
            "event_type": "subjective_assessment.human_reassessment_initialized",
            "limit": 100,
        },
    )
    indexed = {item["entity_id"]: item for item in page["items"]}
    assert indexed[second["version_id"]]["source_version_id"] == first["version_id"]
    assert indexed[third["version_id"]]["source_version_id"] == second["version_id"]
    for private in (
        "Fresh independent proposal after rejected reassessment",
        "Original synthetic reassessment requiring correction",
        "Private HUMAN technical audio acceptance rationale.",
    ):
        assert private not in json.dumps(page)


def test_generic_writing_actions_and_correction_cannot_shadow_human_speaking(speaking):
    from app.writing_assessment.service import RubricError, SubjectiveAssessmentService

    state = speaking.submitted()
    created = speaking.initialize(state)
    proposed = speaking.propose(state, created)
    admin = speaking.login("admin@demo.prufai.local")
    base = speaking.org + f"/subjective-assessments/{created['version_id']}"
    for action, body in (
        (
            "human-proposal",
            {
                "operation_id": str(uuid.uuid4()),
                "expected_checksum": proposed["checksum"],
                "criteria": speaking.criteria(state),
            },
        ),
        (
            "human-approve",
            {"operation_id": str(uuid.uuid4()), "expected_checksum": proposed["checksum"]},
        ),
        ("approve", {}),
        ("propose", {}),
        ("decision", {"decision": "APPROVED"}),
    ):
        speaking.call("POST", base + "/" + action, admin, expected=409, json=body)
    speaking.approve(proposed)
    speaking.session.expire_all()
    source = speaking.session.get(SubjectiveAssessmentVersion, uuid.UUID(created["version_id"]))
    review = speaking.session.scalar(
        select(HumanReviewRequest).where(HumanReviewRequest.entity_id == source.id)
    )
    actor = speaking.session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
    context = build_tenant_context(speaking.session, actor.id, source.assessment.organisation_id)
    with pytest.raises(
        RubricError, match="Speaking reassessment requires its independent successor"
    ):
        SubjectiveAssessmentService.correct(
            speaking.session, context, source, {"TASK": {"points": "3"}}, review
        )
    speaking.session.rollback()
    assert (
        speaking.session.scalar(select(func.count()).select_from(SubjectiveAssessmentVersion)) == 1
    )
