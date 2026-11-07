"""Required S3 runtime proof using isolated Alembic PostgreSQL and real MinIO."""

import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.authorization import build_tenant_context
from app.evaluation.disposable import assert_disposable_database
from app.lid_practice import service as lid
from app.main import app
from app.models import (
    AuditEvent,
    CandidateExamAssignment,
    CandidateExamAttempt,
    CandidateResponseRevision,
    CertificateRecord,
    CertificationAuthorization,
    ExamVersion,
    ExamVersionItemBinding,
    ItemScore,
    LidPracticeForm,
    LidPracticePoolVersion,
    MembershipRole,
    Organisation,
    OrganisationMembership,
    QuestionBankItemVersion,
    ResultRelease,
    ResultVersion,
    Role,
    ScoringRun,
    StandardPackVersion,
    User,
)
from app.models.enums import (
    CertificationAuthorizationStatus,
    OrganisationType,
    QuestionBankLifecycleStatus,
    RoleScope,
)
from app.result_release import service as release_service
from app.runtime import service as runtime
from app.scoring.service import ScoringError, ScoringService
from app.scripts.seed_lid_practice import seed_lid_practice
from tests.test_authoritative_standards_postgresql_minio_integration import (
    configured as _configured,
)
from tests.test_authoritative_standards_postgresql_minio_integration import expert as _expert
from tests.test_authoritative_standards_postgresql_minio_integration import proof as _proof
from tests.test_expert_references_postgresql_integration import PASSWORD

configured = _configured
expert = _expert
proof = _proof
LID = "/api/v1/platform/standards/lid"
CANDIDATE = "/api/v1/candidate"


@pytest.fixture(scope="module")
def practice(proof):
    pins = {}
    for source in proof.http("GET", LID + "/sources"):
        key = source["key"]
        registered = proof.http("POST", f"{LID}/sources/{key}/register")["registered"]
        proof.rights(
            registered,
            "METADATA_ONLY",
            rights_holder=source["rights_holder"],
            licence_type="TEST ONLY bibliographic scope",
            basis_reference="synthetic:s3:metadata-only",
        )
        pins[key] = {
            "version_id": registered["id"],
            "metadata_checksum": registered["metadata_checksum"],
        }
    draft = proof.http("POST", LID + "/blueprints", status=201, json={"sources": pins})
    org = proof.headers["revoked"]["X-PrufAI-Organisation-ID"]
    with proof.expert.factory() as session:
        candidate_role = session.scalar(
            select(Role).where(Role.key == "EXAMINER", Role.scope == RoleScope.TENANT)
        )
        assert candidate_role is not None
        other_org = Organisation(
            name="Separate S3 synthetic institution",
            slug="s3-outside-" + uuid.uuid4().hex,
            organisation_type=OrganisationType.LANGUAGE_SCHOOL,
        )
        session.add(other_org)
        session.flush()
        for actor, scope in (("candidate", uuid.UUID(org)), ("observer", other_org.id)):
            membership = OrganisationMembership(
                organisation_id=scope, user_id=proof.expert.actors[actor]
            )
            session.add(membership)
            session.flush()
            session.add(MembershipRole(membership_id=membership.id, role_id=candidate_role.id))
        outsider = session.get(User, proof.expert.actors["observer"])
        assert outsider is not None
        outsider_email = outsider.email
        outside_id = str(other_org.id)
        blueprint = session.get(StandardPackVersion, uuid.UUID(draft["id"]))
        assert blueprint is not None
        database = urlparse(proof.expert.database_url).path.removeprefix("/")
        arguments = {
            "disposable_database": database,
            "blueprint": blueprint,
            "author": build_tenant_context(session, proof.expert.actors["revoked"], uuid.UUID(org)),
            "reviewer": build_tenant_context(
                session, proof.expert.actors["approver"], uuid.UUID(org)
            ),
        }
        version = seed_lid_practice(session, **arguments)
        assert seed_lid_practice(session, **arguments).id == version.id
        version_id = str(version.id)
        session.commit()
    proof.headers["candidate"]["X-PrufAI-Organisation-ID"] = org
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login", json={"email": outsider_email, "password": PASSWORD}
        )
        assert response.status_code == 200
    proof.headers["outsider"] = {
        "Authorization": "Bearer " + response.json()["access_token"],
        "X-PrufAI-Organisation-ID": outside_id,
    }
    return org, version_id


def _assignment(proof, practice):
    org, version_id = practice
    return proof.http(
        "POST",
        f"/api/v1/organisations/{org}/exam-assignments",
        actor="revoked",
        status=201,
        json={
            "candidate_user_id": str(proof.expert.actors["candidate"]),
            "exam_version_id": version_id,
        },
    )["id"]


def _candidate(proof, method, path, *, status=200, json=None):
    return proof.http(method, CANDIDATE + path, status=status, actor="candidate", json=json)


def _prepare(proof, assignment_id):
    return _candidate(
        proof,
        "POST",
        f"/exam-assignments/{assignment_id}/attempts",
        status=201,
        json={"bundesland": "DE-BE"},
    )


def _items(state):
    return [
        item
        for module in state["runtime_plan"]["modules"]
        for section in module["sections"]
        for task in section["tasks"]
        for item in task["items"]
    ]


def _correct_option(item):
    number = int(item["version_label"].split("-")[-2])
    return f"option-{(number - 1) % 4 + 1}"


def test_prepare_demo_replays_without_replacing_candidate_work(configured):
    controller_url = os.environ["POSTGRES_INTEGRATION_URL"]
    controller_name = assert_disposable_database(controller_url)
    controller = make_url(controller_url).set(drivername="postgresql")
    database = "prufai_eval2_lidseed_" + uuid.uuid4().hex
    target = make_url(controller_url).set(database=database)
    url = target.render_as_string(hide_password=False)
    assert_disposable_database(url, database)
    root = Path(__file__).resolve().parents[3]
    env = {
        **os.environ,
        "PYTHONPATH": str(root / "services/api"),
        "ENVIRONMENT": "development",
        "DATABASE_URL": url,
        "POSTGRES_INTEGRATION_URL": url,
        "PRUFAI_DISPOSABLE_DATABASE": database,
        "PRUFAI_DEMO_ENABLED": "1",
        "PRUFAI_DEMO_PASSWORD": uuid.uuid4().hex,
    }
    seed = [sys.executable, "-m", "app.scripts.seed_lid_practice", "--prepare-demo"]

    def run(command):
        return subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, timeout=90
        )

    with psycopg.connect(
        controller.render_as_string(hide_password=False), autocommit=True
    ) as control:
        assert control.execute("SELECT current_database()").fetchone() == (controller_name,)
        control.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        engine = create_engine(url)
        try:
            before_migration = run(seed)
            assert before_migration.returncode != 0
            assert "LID_SYNTHETIC_FIXTURE_ALEMBIC_HEAD_REQUIRED" in before_migration.stderr
            migrated = run([sys.executable, "scripts/migrate-evaluation-disposable.py"])
            assert migrated.returncode == 0, migrated.stderr
            first = run(seed)
            assert first.returncode == 0, first.stderr
            fixture = json.loads(first.stdout.splitlines()[-1])
            assignment_id = uuid.UUID(fixture["candidate_assignment_id"])
            with Session(engine) as session:
                assignment = session.get(CandidateExamAssignment, assignment_id)
                assert assignment is not None and assignment.attempts == []
                candidate = build_tenant_context(
                    session, assignment.candidate_user_id, assignment.organisation_id
                )
                runtime_service = runtime.CandidateRuntimeService
                attempt = runtime_service.create_attempt(
                    session, candidate, assignment_id, bundesland="DE-BE"
                )
                assert attempt.started_at is None
                runtime_service.start(session, candidate, attempt.id)
                first_item = _items({"runtime_plan": attempt.runtime_plan})[0]
                runtime_service.save_response(
                    session,
                    candidate,
                    attempt.id,
                    uuid.UUID(first_item["binding_id"]),
                    payload={"option_id": _correct_option(first_item)},
                    client_operation_id=uuid.uuid4(),
                    expected_revision=0,
                )
                runtime_service.submit(session, candidate, attempt.id)
                attempt_id, submitted_at = attempt.id, attempt.submitted_at
                administrator = build_tenant_context(
                    session, assignment.created_by_user_id, assignment.organisation_id
                )
                run_id = ScoringService.score(session, administrator, attempt_id).id
                result_id = session.scalar(
                    select(ResultVersion.id).where(ResultVersion.scoring_run_id == run_id)
                )
                assert result_id is not None
                session.commit()

            replay = run(seed)
            assert replay.returncode == 0, replay.stderr
            assert json.loads(replay.stdout.splitlines()[-1]) == fixture
            with Session(engine) as session:
                assignment = session.get(CandidateExamAssignment, assignment_id)
                assert assignment is not None and [a.id for a in assignment.attempts] == [
                    attempt_id
                ]
                assert assignment.attempts[0].submitted_at == submitted_at
                revision = session.scalar(
                    select(CandidateResponseRevision).where(
                        CandidateResponseRevision.attempt_id == attempt_id
                    )
                )
                assert revision is not None and revision.payload["option_id"] == _correct_option(
                    first_item
                )
                assert list(
                    session.scalars(
                        select(ResultVersion.id).where(ResultVersion.scoring_run_id == run_id)
                    )
                ) == [result_id]
                assignment.maximum_attempts = 2
                session.commit()
            conflict = run(seed)
            assert conflict.returncode != 0
            assert "LID_SYNTHETIC_FIXTURE_ASSIGNMENT_CONFLICT" in conflict.stderr
            with Session(engine) as session:
                assignment = session.get(CandidateExamAssignment, assignment_id)
                assert assignment is not None and assignment.maximum_attempts == 2
        finally:
            engine.dispose()
            control.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


def _save(proof, attempt, item, option, *, operation=None, status=200):
    return _candidate(
        proof,
        "PUT",
        f"/exam-attempts/{attempt}/responses/{item['binding_id']}",
        status=status,
        json={
            "client_operation_id": str(operation or uuid.uuid4()),
            "expected_revision": 0,
            "payload": {"option_id": option},
        },
    )


def _score(proof, practice, attempt, *, status=200):
    return proof.http(
        "POST",
        f"/api/v1/organisations/{practice[0]}/exam-attempts/{attempt}/score",
        actor="revoked",
        status=status,
    )


def _assert_legacy_issuance_denied(proof, organisation_id, result, monkeypatch):
    prefix = f"/api/v1/organisations/{organisation_id}"
    result_path = f"{prefix}/result-versions/{result['result_version_id']}"
    assert (
        proof.http("POST", result_path + "/request-review", actor="revoked")["status"] == "PENDING"
    )
    assert proof.http("POST", result_path + "/approve", actor="revoked")["status"] == "APPROVED"
    published = proof.http(
        "POST",
        result_path + "/release",
        actor="revoked",
        json={"operation_id": "lid-practice-release"},
    )
    assert published["release_status"] == "RELEASED"
    release_id = uuid.UUID(published["release_id"])

    with proof.expert.factory() as session:
        release = session.get(ResultRelease, release_id)
        assert release is not None and release.classification == "PRACTICE"
        pack = session.get(StandardPackVersion, release.standard_pack_version_id)
        assert pack is not None
        now = datetime.now(timezone.utc)
        effective_from = now - timedelta(minutes=1)
        effective_until = now + timedelta(minutes=1)
        evidence = "TEST ONLY legacy fixture with an otherwise exact scope"
        # Model the pre-guard state directly in the disposable test database.
        # The actual authorization endpoint already refuses this LiD pack.
        authorization = CertificationAuthorization(
            organisation_id=release.organisation_id,
            assessment_scope=release.classification,
            provider_scope=pack.standard_pack.code,
            standard_pack_version_id=pack.id,
            operation_id="lid-test-only-legacy-authorization",
            classification=release.classification,
            effective_from=effective_from,
            effective_until=effective_until,
            status=CertificationAuthorizationStatus.ACTIVE,
            evidence_reference=evidence,
            approved_by_user_id=proof.expert.actors["approver"],
            checksum=release_service._checksum(
                {
                    "scope": release.classification,
                    "provider": pack.standard_pack.code,
                    "pack": str(pack.id),
                    "evidence": evidence,
                    "effective_from": effective_from.isoformat(),
                    "effective_until": effective_until.isoformat(),
                }
            ),
            version=1,
        )
        session.add(authorization)
        session.flush()
        authorization_id = authorization.id
        session.commit()

    enabled_settings = proof.settings.model_copy(update={"certification_issuance_enabled": True})
    with monkeypatch.context() as patch:
        patch.setattr(release_service, "get_settings", lambda: enabled_settings)
        denied = proof.http(
            "POST",
            f"{prefix}/releases/{release_id}/certificate?authorization_id={authorization_id}",
            actor="revoked",
            status=409,
            json={"operation_id": "lid-legacy-issuance-denied"},
        )
    assert denied["detail"] == "CERTIFICATION.NOT_AUTHORIZED"
    with proof.expert.factory() as session:
        assert session.get(CertificationAuthorization, authorization_id) is not None
        assert (
            session.scalar(
                select(CertificateRecord).where(CertificateRecord.release_id == release_id)
            )
            is None
        )


def test_real_private_form_and_canonical_scoring(proof, practice, monkeypatch):
    assignment = _assignment(proof, practice)
    assignment_path = f"/exam-assignments/{assignment}"
    create_path = assignment_path + "/attempts"
    choices = _candidate(proof, "GET", assignment_path)["lid_practice"]["bundeslaender"]
    assert len({state["code"] for state in choices}) == 16
    assert [state["code"] for state in choices if state["available"]] == ["DE-BE"]
    assert _candidate(proof, "POST", create_path, status=400)["detail"] == "LID_BUNDESLAND_REQUIRED"
    assert (
        _candidate(proof, "POST", create_path, status=400, json={"bundesland": "DE-XX"})["detail"]
        == "LID_INVALID_BUNDESLAND"
    )
    assert (
        _candidate(proof, "POST", create_path, status=400, json={"bundesland": "DE-BY"})["detail"]
        == "CONTENT_UNAVAILABLE"
    )
    assert proof.http(
        "POST",
        CANDIDATE + create_path,
        actor="outsider",
        status=403,
        json={"bundesland": "DE-BE"},
    )
    attempt = _prepare(proof, assignment)["id"]
    assert _prepare(proof, assignment)["id"] == attempt
    path = f"/exam-attempts/{attempt}"
    ready = _candidate(proof, "GET", path + "/runtime")
    assert ready["runtime_plan"]["modules"] == []
    assert ready["runtime_plan"]["lid_practice"]["bundesland"] == "DE-BE"
    state = _candidate(proof, "POST", path + "/start")
    assert state["runtime_plan"]["lid_practice"]["attempt_id"] == attempt
    assert datetime.fromisoformat(state["deadline_at"]) - datetime.fromisoformat(
        state["started_at"]
    ) == timedelta(minutes=60)
    assert state["runtime_plan_checksum"] == ready["runtime_plan_checksum"]
    assert datetime.fromisoformat(
        _candidate(proof, "POST", path + "/start")["deadline_at"]
    ) == datetime.fromisoformat(state["deadline_at"])
    items = _items(state)
    assert Counter(item["lid_pool_scope"] for item in items) == {
        "GENERAL_DE": 30,
        "BUNDESLAND_SPECIFIC": 3,
    }
    assert len({item["item_version_id"] for item in items}) == 33
    for item in items:
        assert item["assessment_family"] == "INTEGRATION_KNOWLEDGE"
        assert set(item["content"]) == {"prompt", "options"}
        assert item["content"]["prompt"].startswith("ORIGINAL SYNTHETIC TEST CONTENT")
        assert len(item["content"]["options"]) == 4
        assert all(
            set(option) == {"option_id", "display_text"} for option in item["content"]["options"]
        )

    proof.http("GET", CANDIDATE + path + "/runtime", actor="outsider", status=403)
    proof.http("GET", CANDIDATE + path + "/runtime", actor="revoked", status=403)
    proof.http(
        "POST",
        f"/api/v1/organisations/{practice[0]}/exam-attempts/{attempt}/score",
        actor="candidate",
        status=403,
    )
    assert (
        _candidate(proof, "GET", path + "/lid-practice-result", status=404)["detail"]
        == "LID_PRACTICE_RESULT_PENDING"
    )

    first = items[0]
    operation = uuid.uuid4()
    right = _correct_option(first)
    first_saved = _save(proof, attempt, first, right, operation=operation)
    assert _save(proof, attempt, first, right, operation=operation) == first_saved
    _save(proof, attempt, first, "incorrect-option", status=400)
    wrong = next(
        row["option_id"] for row in first["content"]["options"] if row["option_id"] != right
    )
    assert (
        _save(proof, attempt, first, wrong, operation=operation, status=409)["detail"]
        == "RUNTIME.IDEMPOTENCY_KEY_REUSED"
    )
    _save(proof, attempt, {**first, "binding_id": str(uuid.uuid4())}, right, status=400)
    resumed = _candidate(proof, "GET", path + "/runtime")
    assert datetime.fromisoformat(resumed["deadline_at"]) == datetime.fromisoformat(
        state["deadline_at"]
    )
    assert resumed["responses"] == [
        {"binding_id": first["binding_id"], "revision": 1, "payload": {"option_id": right}}
    ]

    for index, item in enumerate(items[1:], 1):
        correct = _correct_option(item)
        option = (
            correct
            if index < 15
            else next(
                row["option_id"]
                for row in item["content"]["options"]
                if row["option_id"] != correct
            )
        )
        _save(proof, attempt, item, option)
    with ThreadPoolExecutor(max_workers=2) as worker:
        a, b = worker.map(lambda _: _candidate(proof, "POST", path + "/submit"), range(2))
    assert a["id"] == b["id"] == attempt
    assert a["status"] == b["status"] == "SUBMITTED"
    assert datetime.fromisoformat(a["submitted_at"]) == datetime.fromisoformat(b["submitted_at"])
    _save(proof, attempt, first, right, status=409)
    run = _score(proof, practice, attempt)
    assert _score(proof, practice, attempt) == run
    result = _candidate(proof, "GET", path + "/lid-practice-result")
    assert result["scoring_run_id"] == run["id"]
    assert result["raw_correct"] == 15 and result["total_items"] == result["answered_count"] == 33
    assert result["official_outcome"] == "UNDETERMINED"
    assert result["administrative_recognition"] == "NOT_ESTABLISHED"
    assert result["certification"] == "NOT_CERTIFIED"
    assert [(row["threshold"], row["numerical_outcome"]) for row in result["contexts"]] == [
        (15, "THRESHOLD_MET"),
        (17, "BELOW_THRESHOLD"),
    ]
    assert all(row["citations"] and row["rule_checksum"] for row in result["contexts"])
    for row in result["contexts"]:
        for citation in row["citations"]:
            assert (
                not {"rights_decision_checksum", "rights_status", "legal_review_status"}
                & citation.keys()
            )
    proof.http("GET", CANDIDATE + path + "/lid-practice-result", actor="outsider", status=403)
    proof.http("GET", CANDIDATE + path + "/lid-practice-result", actor="revoked", status=403)

    with proof.expert.factory() as session:
        stored = session.get(CandidateExamAttempt, uuid.UUID(attempt))
        form = session.get(LidPracticeForm, uuid.UUID(practice[1]))
        rv = session.get(ResultVersion, uuid.UUID(result["result_version_id"]))
        assert stored is not None and form is not None and rv is not None
        assert stored.runtime_plan["lid_practice"]["form_checksum"] == form.checksum
        assert rv.rule_provenance["lid_practice_preview"]["contexts"] == result["contexts"]
        assert rv.outcome.value == "UNDETERMINED"
        scores = list(
            session.scalars(
                select(ItemScore).where(ItemScore.scoring_run_id == uuid.UUID(run["id"]))
            )
        )
        assert len(scores) == 33
        assert sum(score.awarded_points or 0 for score in scores) == 15
        events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.entity_id.in_([attempt, practice[1], run["id"]])
                )
            )
        )
        assert {event.event_type for event in events} >= {
            "lid.practice.form_generated",
            "exam_attempt.created",
            "exam_attempt.started",
            "exam_attempt.submitted",
            "scoring.run.completed",
            "lid.practice.preview_calculated",
        }
        assert sum(event.event_type == "exam_attempt.submitted" for event in events) == 1
        for event in events:
            assert not {"correct_option_ids", "payload", "prompt", "options"}.intersection(
                event.metadata_json or {}
            )
        stored.runtime_plan = {**stored.runtime_plan, "duration_seconds": 9000}
        with pytest.raises(DBAPIError):
            session.flush()
        session.rollback()

    _assert_legacy_issuance_denied(proof, practice[0], result, monkeypatch)


def test_incomplete_and_timeout_fail_closed(proof, practice, monkeypatch):
    incomplete = _prepare(proof, _assignment(proof, practice))["id"]
    path = f"/exam-attempts/{incomplete}"
    state = _candidate(proof, "POST", path + "/start")
    first = _items(state)[0]
    _save(proof, incomplete, first, _correct_option(first))
    _candidate(proof, "POST", path + "/submit")
    _score(proof, practice, incomplete)
    result = _candidate(proof, "GET", path + "/lid-practice-result")
    assert result["raw_correct"] == result["answered_count"] == 1
    assert {row["numerical_outcome"] for row in result["contexts"]} == {"UNDETERMINED"}

    expiring = _prepare(proof, _assignment(proof, practice))["id"]
    path = f"/exam-attempts/{expiring}"
    state = _candidate(proof, "POST", path + "/start")
    monkeypatch.setattr(
        runtime, "_now", lambda: datetime.fromisoformat(state["deadline_at"]) + timedelta(seconds=1)
    )
    assert _candidate(proof, "POST", path + "/heartbeat")["status"] == "EXPIRED"
    _candidate(proof, "POST", path + "/submit", status=409)


def test_preexisting_fixture_cannot_run_in_production(proof, practice, monkeypatch):
    assignment = _assignment(proof, practice)
    attempt = _prepare(proof, assignment)["id"]
    path = f"/exam-attempts/{attempt}"
    state = _candidate(proof, "POST", path + "/start")
    first = _items(state)[0]
    with monkeypatch.context() as patch:
        patch.setattr(lid, "get_settings", lambda: SimpleNamespace(environment="production"))
        assert all(row["id"] != assignment for row in _candidate(proof, "GET", "/exam-assignments"))
        _candidate(proof, "GET", f"/exam-assignments/{assignment}", status=404)
        _candidate(proof, "GET", path + "/runtime", status=403)
        _candidate(proof, "POST", path + "/submit", status=403)
        _save(proof, attempt, first, _correct_option(first), status=403)
    assert _candidate(proof, "GET", path + "/runtime")["status"] == "IN_PROGRESS"


def test_changed_pool_or_item_cannot_deliver_or_score(proof, practice):
    attempt = _prepare(proof, _assignment(proof, practice))["id"]
    path = f"/exam-attempts/{attempt}"
    _candidate(proof, "POST", path + "/start")
    _candidate(proof, "POST", path + "/submit")

    with proof.expert.factory() as session:
        version = session.get(ExamVersion, uuid.UUID(practice[1]))
        form = session.get(LidPracticeForm, uuid.UUID(practice[1]))
        assert version is not None and form is not None
        general = session.get(LidPracticePoolVersion, form.general_pool_version_id)
        assert general is not None
        general.manifest = {**general.manifest, "items": general.manifest["items"][:-1]}
        with pytest.raises(ValueError, match="CONTENT_UNAVAILABLE"):
            lid.select_items(general, form.selection_seed, lid.GENERAL_COUNT)
        with pytest.raises(DBAPIError):
            session.flush()
        session.rollback()

    with proof.expert.factory() as session:
        form = session.get(LidPracticeForm, uuid.UUID(practice[1]))
        assert form is not None
        first = form.manifest["items"][0]
        state_item = form.manifest["items"][-1]
        binding = session.get(ExamVersionItemBinding, uuid.UUID(first["binding_id"]))
        assert binding is not None
        binding.question_bank_item_version_id = uuid.UUID(state_item["item_version_id"])
        with pytest.raises(DBAPIError):
            session.flush()
        session.rollback()

    with proof.expert.factory() as session:
        form = session.get(LidPracticeForm, uuid.UUID(practice[1]))
        assert form is not None
        item = session.get(
            QuestionBankItemVersion, uuid.UUID(form.manifest["items"][0]["item_version_id"])
        )
        assert item is not None
        item.status = QuestionBankLifecycleStatus.RETIRED
        session.flush()
        with pytest.raises(ScoringError, match="LID_PRACTICE_FORM_PROVENANCE_INVALID"):
            ScoringService.score(
                session,
                build_tenant_context(
                    session, proof.expert.actors["revoked"], uuid.UUID(practice[0])
                ),
                uuid.UUID(attempt),
            )
        assert (
            session.scalar(select(ScoringRun).where(ScoringRun.attempt_id == uuid.UUID(attempt)))
            is None
        )
        session.rollback()
