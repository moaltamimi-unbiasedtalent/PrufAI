"""One real JWT/API practice journey on an Alembic-created PostgreSQL database."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api import auth_settings_dependency
from app.auth.service import AuthenticationService
from app.authorization import build_tenant_context
from app.config import Settings, get_settings
from app.db import get_db_session
from app.main import app
from app.models import (
    AssessmentResult,
    CandidateExamAttempt,
    CandidateResponse,
    CandidateResponseRevision,
    CertificateRecord,
    CertificationAuthorization,
    ItemScore,
    ResultCorrection,
    ResultRelease,
    ResultVersion,
    ScoringRun,
    StatementOfResults,
    User,
)
from app.models.enums import OrganisationType
from app.result_governance.service import ResultGovernanceError, ResultGovernanceService
from app.scripts.seed_demo import PASSWORD, PLATFORM_ADMIN_EMAIL, reconcile_demo
from app.services.identity import IdentityService
from app.services.organisations import OrganisationService
from tests.alembic_helpers import repository_alembic_head

REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="POSTGRES_INTEGRATION_URL is required for the integrated practice journey",
)
PLATFORM = "/api/v1/platform/control-center"


def _url() -> str:
    url = os.getenv("POSTGRES_INTEGRATION_URL")
    if not url:
        pytest.fail("Required integrated practice gate needs POSTGRES_INTEGRATION_URL")
    return url


@dataclass
class Journey:
    session: Session
    client: TestClient
    settings: Settings
    identifiers: dict[str, str]
    email: str
    candidate_id: uuid.UUID
    other_email: str
    admin_id: uuid.UUID

    @property
    def organisation_id(self) -> str:
        return self.identifiers["organisation_id"]

    @property
    def org(self) -> str:
        return f"/api/v1/organisations/{self.organisation_id}"

    def login(self, email: str, *, tenant: bool = True) -> dict[str, str]:
        response = self.client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        if tenant:
            headers["X-PrufAI-Organisation-ID"] = self.organisation_id
        return headers

    def request(self, method: str, path: str, headers: dict[str, str], *, expected=200, **kwargs):
        response = self.client.request(method, path, headers=headers, **kwargs)
        assert response.status_code == expected, (method, path, response.status_code, response.text)
        return response.json() if response.content else None


@pytest.fixture
def journey() -> Iterator[Journey]:
    engine = create_engine(_url())
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_rate_limit_backend="memory",
        auth_jwt_secret="integrated-practice-jwt-test-" + "x" * 40,
        auth_rate_limit_login_attempts=100,
    )
    previous = dict(app.dependency_overrides)

    def database():
        with factory() as request_session:
            yield request_session

    with factory() as session:
        identifiers = reconcile_demo(
            session, Settings(_env_file=None, environment="development"), enabled=True
        )
        admin = session.scalar(
            select(User).where(User.normalized_email == "admin@demo.prufai.local")
        )
        assert admin is not None
        context = build_tenant_context(session, admin.id, uuid.UUID(identifiers["organisation_id"]))
        users = []
        for label in ("golden", "other"):
            user = IdentityService.create_user(
                session,
                email=f"integrated-{label}-{uuid.uuid4()}@example.test",
                display_name=f"Integrated {label} candidate",
            )
            AuthenticationService(settings).set_password(session, user, PASSWORD)
            membership = OrganisationService.create_membership(
                session, context, organisation_id=context.organisation_id, user_id=user.id
            )
            OrganisationService.assign_membership_role(
                session, context, membership_id=membership.id, role_key="EXAMINER"
            )
            users.append(user)
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
                    users[0].email,
                    users[0].id,
                    users[1].email,
                    admin.id,
                )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(previous)
    engine.dispose()


def submitted_and_scored(scenario: Journey) -> dict[str, Any]:
    admin = scenario.login("admin@demo.prufai.local")
    options = scenario.request("GET", scenario.org + "/exam-assignment-options", admin)
    assert str(scenario.candidate_id) in {row["id"] for row in options["candidates"]}
    chosen = next(
        row
        for row in options["exam_versions"]
        if row["id"] == scenario.identifiers["golden_exam_version_id"]
    )
    assert chosen["classification"] == "PRACTICE"
    assignment = scenario.request(
        "POST",
        scenario.org + "/exam-assignments",
        admin,
        expected=201,
        json={"candidate_user_id": str(scenario.candidate_id), "exam_version_id": chosen["id"]},
    )
    candidate = scenario.login(scenario.email)
    assert scenario.request("GET", "/api/v1/candidate/results", candidate) == []
    assert scenario.request("GET", "/api/v1/candidate/releases", candidate) == []
    attempt = scenario.request(
        "POST",
        f"/api/v1/candidate/exam-assignments/{assignment['id']}/attempts",
        candidate,
        expected=201,
    )
    attempt_path = f"/api/v1/candidate/exam-attempts/{attempt['id']}"
    runtime = scenario.request("POST", attempt_path + "/start", candidate)
    plan = runtime["runtime_plan"]
    assert plan["exam_version_id"] == chosen["id"]
    assert plan["standard_pack_version_id"] == scenario.identifiers["golden_pack_version_id"]
    item = plan["modules"][0]["sections"][0]["tasks"][0]["items"][0]
    assert item["item_version_id"] == scenario.identifiers["golden_item_version_id"]
    assert "NOT OFFICIAL MATERIAL" in item["content"]["prompt"]
    assert "correct_option_ids" not in item["content"]
    save_path = attempt_path + f"/responses/{item['binding_id']}"
    first_body = {
        "client_operation_id": str(uuid.uuid4()),
        "expected_revision": 0,
        "payload": {"option_id": "b"},
    }
    first = scenario.request("PUT", save_path, candidate, json=first_body)
    assert first["revision"] == 1
    assert scenario.request("PUT", save_path, candidate, json=first_body) == first
    second = scenario.request(
        "PUT",
        save_path,
        candidate,
        json={
            "client_operation_id": str(uuid.uuid4()),
            "expected_revision": 1,
            "payload": {"option_id": "a"},
        },
    )
    assert second["revision"] == 2
    reloaded = scenario.request("GET", attempt_path + "/runtime", candidate)
    assert reloaded["attempt_id"] == attempt["id"]
    assert reloaded["responses"] == [
        {"binding_id": item["binding_id"], "revision": 2, "payload": {"option_id": "a"}}
    ]
    scenario.request(
        "PUT",
        save_path,
        candidate,
        expected=409,
        json={
            "client_operation_id": str(uuid.uuid4()),
            "expected_revision": 1,
            "payload": {"option_id": "b"},
        },
    )
    score_path = scenario.org + f"/exam-attempts/{attempt['id']}/score"
    scenario.request("POST", score_path, admin, expected=409)
    submitted = scenario.request("POST", attempt_path + "/submit", candidate)
    assert submitted["status"] == "SUBMITTED"
    replayed_submission = scenario.request("POST", attempt_path + "/submit", candidate)
    assert replayed_submission["id"] == submitted["id"]
    assert replayed_submission["status"] == submitted["status"]
    assert datetime.fromisoformat(replayed_submission["submitted_at"].replace("Z", "+00:00")) == (
        datetime.fromisoformat(submitted["submitted_at"].replace("Z", "+00:00"))
    )
    scenario.request(
        "PUT",
        save_path,
        candidate,
        expected=409,
        json={
            "client_operation_id": str(uuid.uuid4()),
            "expected_revision": 2,
            "payload": {"option_id": "b"},
        },
    )
    run = scenario.request("POST", score_path, admin)
    assert run["status"] == "COMPLETED"
    assert scenario.request("POST", score_path, admin)["id"] == run["id"]
    scenario.session.expire_all()
    result = scenario.session.scalar(
        select(AssessmentResult).where(AssessmentResult.attempt_id == uuid.UUID(attempt["id"]))
    )
    assert result is not None
    projection = scenario.request("GET", scenario.org + f"/results/{result.id}", admin)
    assert projection["scoring_run_id"] == run["id"]
    assert projection["version_number"] == 1
    assert projection["raw_awarded_points"] == "1.0000"
    assert projection["raw_maximum_points"] == "1.0000"
    assert projection["status"] == "REVIEW_REQUIRED"
    assert projection["review"] is None
    assert scenario.request("GET", "/api/v1/candidate/results", candidate) == []
    return {
        "admin": admin,
        "candidate": candidate,
        "assignment": assignment,
        "attempt": attempt,
        "item": item,
        "run": run,
        "result": projection,
        "result_id": str(result.id),
    }


def release_and_statement(scenario: Journey, state: dict[str, Any]) -> dict[str, Any]:
    version_id = state["result"]["result_version_id"]
    path = scenario.org + f"/result-versions/{version_id}"
    operation = str(uuid.uuid4())
    scenario.request(
        "POST", path + "/release", state["admin"], expected=409, json={"operation_id": operation}
    )
    scenario.request("POST", path + "/approve", state["admin"], expected=409)
    review = scenario.request("POST", path + "/request-review", state["admin"])
    reloaded = scenario.request(
        "GET", scenario.org + f"/results/{state['result_id']}", state["admin"]
    )
    assert reloaded["review"] == {
        "id": review["id"],
        "status": "PENDING",
        "reviewed_checksum": state["result"]["checksum"],
    }
    scenario.request("POST", path + "/approve", state["admin"])
    released = scenario.request(
        "POST", path + "/release", state["admin"], json={"operation_id": operation}
    )
    assert (
        scenario.request(
            "POST", path + "/release", state["admin"], json={"operation_id": operation}
        )
        == released
    )
    scenario.request(
        "POST",
        path + "/release",
        state["admin"],
        expected=409,
        json={"operation_id": str(uuid.uuid4())},
    )
    statement_path = scenario.org + f"/releases/{released['release_id']}/statement"
    statement_operation = str(uuid.uuid4())
    statement = scenario.request(
        "POST", statement_path, state["admin"], json={"operation_id": statement_operation}
    )
    assert (
        scenario.request(
            "POST", statement_path, state["admin"], json={"operation_id": statement_operation}
        )
        == statement
    )
    payload = statement["payload"]
    assert payload["classification"] == "PRACTICE"
    assert payload["result_version_id"] == version_id
    assert payload["scoring_run_id"] == state["run"]["id"]
    assert payload["standard_pack_version_id"] == scenario.identifiers["golden_pack_version_id"]
    assert payload["attempt_id"] == state["attempt"]["id"]
    assert payload["candidate_user_id"] == str(scenario.candidate_id)
    assert payload["issued_by_user_id"] == str(scenario.admin_id)
    assert payload["result_checksum"] == state["result"]["checksum"]
    assert Decimal(payload["raw_awarded_points"]) == Decimal("1")
    assert (
        statement["checksum"]
        == hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
    )
    owned = scenario.request(
        "GET", f"/api/v1/candidate/releases/{released['release_id']}", state["candidate"]
    )
    assert owned["result"]["result_version_id"] == version_id
    assert owned["result"]["result_checksum"] == payload["result_checksum"]
    assert owned["released_by_user_id"] == str(scenario.admin_id)
    assert (
        scenario.request(
            "GET",
            f"/api/v1/candidate/releases/{released['release_id']}/statement",
            state["candidate"],
        )["id"]
        == statement["id"]
    )
    return {**state, "release": released, "statement": statement, "release_operation": operation}


def test_integrated_practice_database_is_alembic_migrated() -> None:
    with psycopg.connect(_url().replace("postgresql+psycopg://", "postgresql://")) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [
            (repository_alembic_head(),)
        ]
        assert connection.execute(
            "SELECT extname FROM pg_extension WHERE extname='vector'"
        ).fetchone()


def test_three_app_practice_journey_and_rejected_appeal_preserve_exact_history(journey: Journey):
    platform = journey.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    packs = journey.request("GET", PLATFORM + "/standards?limit=100", platform)["items"]
    pack = next(row for row in packs if row["id"] == journey.identifiers["golden_pack_version_id"])
    assert pack["assessment_family"] == "LANGUAGE" and pack["language"] == "de"
    assert pack["supported_levels"] == ["B1"]
    exams = journey.request(
        "GET", PLATFORM + f"/exams?organisation_id={journey.organisation_id}", platform
    )
    assert journey.identifiers["golden_exam_version_id"] in {row["id"] for row in exams["items"]}
    state = release_and_statement(journey, submitted_and_scored(journey))
    version_id = state["result"]["result_version_id"]
    before = copy.deepcopy(state["statement"])
    appeal = journey.request(
        "POST",
        f"/api/v1/candidate/results/{version_id}/appeals",
        state["candidate"],
        json={
            "category": "SCORING",
            "grounds": "SYNTHETIC DEMO appeal: please verify the pinned answer.",
            "operation_id": str(uuid.uuid4()),
        },
    )
    assert appeal["challenged_result_version_id"] == version_id
    queued = journey.request("GET", journey.org + "/appeals", state["admin"])
    assert appeal["id"] in {row["id"] for row in queued}
    appeal_path = journey.org + f"/appeals/{appeal['id']}"
    evidence = journey.request("GET", appeal_path + "/evidence", state["admin"])
    assert evidence["result_version_id"] == version_id
    assert evidence["scoring_run_id"] == state["run"]["id"]
    assert evidence["evidence_bundle_checksum"] == appeal["evidence_bundle_checksum"]
    assert evidence["matches_pinned_bundle"] is True
    assert evidence["total_items"] == evidence["supported_items"] == 1
    assert evidence["unsupported_items"] == 0 and evidence["truncated"] is False
    objective = evidence["items"][0]
    assert objective["item_version_id"] == journey.identifiers["golden_item_version_id"]
    assert objective["response_revision_number"] == 2
    assert objective["response"] == {"option_id": "a"}
    assert objective["correct_option_ids"] == ["a"]
    assert Decimal(objective["awarded_points"]) == Decimal("1")
    assert Decimal(objective["maximum_points"]) == Decimal("1")
    assert objective["content_truncated"] is False
    assert "NOT OFFICIAL MATERIAL" in objective["prompt"]
    assert all(key not in evidence for key in ("snapshot_json", "state", "metadata", "storage_key"))
    journey.request("POST", appeal_path + "/review", state["admin"])
    detail = journey.request("GET", appeal_path, state["admin"])
    adjudication = {
        "appeal_version_number": detail["current_version_number"],
        "appeal_checksum": detail["current_checksum"],
        "evidence_bundle_checksum": detail["evidence_bundle_checksum"],
        "decision": "REJECT",
        "operation_id": str(uuid.uuid4()),
        "reason": "Synthetic review confirms the submitted pinned answer and deterministic score.",
    }
    decided = journey.request(
        "POST", appeal_path + "/adjudicate", state["admin"], json=adjudication
    )
    assert decided["status"] == "ADJUDICATED_REJECTED"
    assert (
        journey.request("POST", appeal_path + "/adjudicate", state["admin"], json=adjudication)
        == decided
    )
    final_appeal = journey.request(
        "GET", f"/api/v1/candidate/appeals/{appeal['id']}", state["candidate"]
    )
    assert final_appeal["status"] == "ADJUDICATED_REJECTED"
    assert final_appeal["corrected_result_version_id"] is None
    journey.session.expire_all()
    run = journey.session.get(ScoringRun, uuid.UUID(state["run"]["id"]))
    version = journey.session.get(ResultVersion, uuid.UUID(version_id))
    assert run is not None and version is not None
    persisted_attempt = journey.session.get(CandidateExamAttempt, uuid.UUID(state["attempt"]["id"]))
    assert persisted_attempt is not None and persisted_attempt.status.value == "SUBMITTED"
    assert persisted_attempt.session is not None
    assert (
        journey.session.scalar(
            select(func.count())
            .select_from(CandidateExamAttempt)
            .where(CandidateExamAttempt.assignment_id == uuid.UUID(state["assignment"]["id"]))
        )
        == 1
    )
    assert run.snapshot is not None
    assert run.snapshot.exam_version_id == persisted_attempt.exam_version_id
    assert run.snapshot.runtime_plan_checksum == persisted_attempt.runtime_plan_checksum
    assert run.snapshot.response_set_checksum == run.response_set_checksum
    assert run.exam_version_id == uuid.UUID(journey.identifiers["golden_exam_version_id"])
    assert run.standard_pack_version_id == uuid.UUID(journey.identifiers["golden_pack_version_id"])
    revisions = list(
        journey.session.scalars(
            select(CandidateResponseRevision)
            .where(CandidateResponseRevision.attempt_id == uuid.UUID(state["attempt"]["id"]))
            .order_by(CandidateResponseRevision.revision_number)
        )
    )
    assert [(row.revision_number, row.payload) for row in revisions] == [
        (1, {"option_id": "b"}),
        (2, {"option_id": "a"}),
    ]
    item_score = journey.session.scalar(select(ItemScore).where(ItemScore.scoring_run_id == run.id))
    assert item_score is not None and item_score.candidate_response_revision_id == revisions[1].id
    assert item_score.question_bank_item_version_id == uuid.UUID(
        journey.identifiers["golden_item_version_id"]
    )
    assert item_score.awarded_points == Decimal("1")
    persisted_response = journey.session.get(CandidateResponse, item_score.candidate_response_id)
    assert persisted_response is not None
    assert persisted_response.attempt_id == persisted_attempt.id
    assert persisted_response.current_revision == 2
    assert persisted_response.current_payload == {"option_id": "a"}
    assert (
        version.checksum == state["result"]["checksum"] and run.checksum == state["run"]["checksum"]
    )
    assert (
        journey.session.scalar(
            select(func.count())
            .select_from(ResultVersion)
            .where(ResultVersion.result_id == version.result_id)
        )
        == 1
    )
    assert (
        journey.session.scalar(
            select(ResultCorrection).where(ResultCorrection.appeal_id == uuid.UUID(appeal["id"]))
        )
        is None
    )
    releases = list(
        journey.session.scalars(
            select(ResultRelease).where(ResultRelease.assessment_result_id == version.result_id)
        )
    )
    assert len(releases) == 1 and releases[0].result_version_id == version.id
    statement = journey.session.get(StatementOfResults, uuid.UUID(before["id"]))
    assert (
        statement is not None
        and statement.payload == before["payload"]
        and statement.checksum == before["checksum"]
    )
    assert (
        journey.session.scalar(
            select(CertificateRecord).where(CertificateRecord.release_id == releases[0].id)
        )
        is None
    )
    assert (
        journey.session.scalar(
            select(CertificationAuthorization).where(
                CertificationAuthorization.organisation_id == uuid.UUID(journey.organisation_id)
            )
        )
        is None
    )
    audit = journey.request(
        "GET", PLATFORM + f"/audit?organisation_id={journey.organisation_id}&limit=100", platform
    )
    assert {
        "exam_assignment.created",
        "result.release_created",
        "statement.created",
        "appeal.adjudicated",
    } <= {row["event_type"] for row in audit["items"]}
    assignment_audit = next(
        row
        for row in audit["items"]
        if (
            row["event_type"] == "exam_assignment.created"
            and row["entity_id"] == state["assignment"]["id"]
        )
    )
    assert assignment_audit["entity_type"] == "CANDIDATE_ASSIGNMENT"
    assert assignment_audit["organisation_id"] == journey.organisation_id
    assert assignment_audit["actor_id"] == str(journey.admin_id)
    assert all(
        "metadata" not in row and "reason" not in row and "payload" not in row
        for row in audit["items"]
    )


def test_cross_role_tenant_isolation_and_stale_release_state_are_enforced(journey: Journey):
    state = release_and_statement(journey, submitted_and_scored(journey))
    version_id = state["result"]["result_version_id"]
    release_id = state["release"]["release_id"]
    appeal = journey.request(
        "POST",
        f"/api/v1/candidate/results/{version_id}/appeals",
        state["candidate"],
        json={
            "category": "SCORING",
            "grounds": "Synthetic security evidence review.",
            "operation_id": str(uuid.uuid4()),
        },
    )
    evidence_path = journey.org + f"/appeals/{appeal['id']}/evidence"
    journey.request("GET", evidence_path, state["candidate"], expected=403)
    assert journey.client.get(evidence_path).status_code == 401
    release_path = journey.org + f"/result-versions/{version_id}/release"
    journey.request(
        "POST",
        release_path,
        state["candidate"],
        expected=403,
        json={"operation_id": state["release_operation"]},
    )
    journey.request(
        "GET", journey.org + "/exam-assignment-options", state["candidate"], expected=403
    )
    journey.request("GET", PLATFORM + "/overview", state["admin"], expected=403)
    other = journey.login(journey.other_email)
    journey.request("GET", evidence_path, other, expected=403)
    journey.request("GET", f"/api/v1/candidate/results/{state['result_id']}", other, expected=404)
    journey.request("GET", f"/api/v1/candidate/releases/{release_id}", other, expected=404)
    journey.request(
        "GET", f"/api/v1/candidate/releases/{release_id}/statement", other, expected=404
    )
    assert journey.client.get(f"/api/v1/candidate/releases/{release_id}").status_code == 401
    platform = journey.session.scalar(
        select(User).where(User.normalized_email == PLATFORM_ADMIN_EMAIL)
    )
    context = build_tenant_context(journey.session, platform.id)
    foreign = OrganisationService.create_organisation(
        journey.session,
        context,
        name="Synthetic isolated foreign organisation",
        slug=f"integrated-foreign-{uuid.uuid4().hex}",
        organisation_type=OrganisationType.OTHER,
    )
    foreign_admin = IdentityService.create_user(
        journey.session,
        email=f"foreign-admin-{uuid.uuid4()}@example.test",
        display_name="Synthetic foreign institution administrator",
    )
    AuthenticationService(journey.settings).set_password(journey.session, foreign_admin, PASSWORD)
    membership = OrganisationService.create_membership(
        journey.session, context, organisation_id=foreign.id, user_id=foreign_admin.id
    )
    OrganisationService.assign_membership_role(
        journey.session, context, membership_id=membership.id, role_key="INSTITUTION_ADMIN"
    )
    foreign_actor = journey.login(foreign_admin.email, tenant=False)
    foreign_actor["X-PrufAI-Organisation-ID"] = str(foreign.id)
    journey.request("GET", evidence_path, foreign_actor, expected=403)
    denied_evidence = journey.client.get(
        f"/api/v1/organisations/{foreign.id}/appeals/{appeal['id']}/evidence", headers=foreign_actor
    )
    assert denied_evidence.status_code == 409
    assert "NOT OFFICIAL MATERIAL" not in denied_evidence.text
    assert "correct_option_ids" not in denied_evidence.text
    journey.request(
        "POST",
        release_path,
        foreign_actor,
        expected=403,
        json={"operation_id": state["release_operation"]},
    )
    foreign_path = f"/api/v1/organisations/{foreign.id}/result-versions/{version_id}/release"
    journey.request(
        "POST",
        foreign_path,
        foreign_actor,
        expected=409,
        json={"operation_id": state["release_operation"]},
    )
    # Even a real platform capability cannot replay another organisation's result in this context.
    actor = journey.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    actor["X-PrufAI-Organisation-ID"] = str(foreign.id)
    foreign_path = f"/api/v1/organisations/{foreign.id}/result-versions/{version_id}/release"
    journey.request(
        "POST", foreign_path, actor, expected=409, json={"operation_id": state["release_operation"]}
    )
    snapshot = copy.deepcopy(state["statement"]["payload"])
    journey.request(
        "POST",
        journey.org + f"/releases/{release_id}/revoke",
        state["admin"],
        json={"reason": "Synthetic stale-state regression", "operation_id": str(uuid.uuid4())},
    )
    journey.request(
        "POST",
        release_path,
        state["admin"],
        expected=409,
        json={"operation_id": state["release_operation"]},
    )
    journey.request(
        "POST",
        journey.org + f"/releases/{release_id}/statement",
        state["admin"],
        expected=409,
        json={"operation_id": str(uuid.uuid4())},
    )
    historical = journey.request(
        "GET", f"/api/v1/candidate/releases/{release_id}/statement", state["candidate"]
    )
    assert historical["payload"] == snapshot and historical["currently_valid"] is False


def test_real_logout_revokes_the_candidate_access_token(journey: Journey):
    headers = journey.login(journey.email)
    csrf = journey.request("GET", "/api/v1/auth/csrf", headers)["csrf_token"]
    journey.request("POST", "/api/v1/auth/logout", {**headers, "X-PrufAI-CSRF": csrf}, expected=204)
    assert (
        journey.client.get("/api/v1/candidate/exam-assignments", headers=headers).status_code == 401
    )


def test_appeal_evidence_uses_pinned_revision_even_if_mutable_current_fields_are_corrupt(
    journey: Journey,
):
    """Adversarial savepoint only: immutable history and result records stay untouched."""
    state = release_and_statement(journey, submitted_and_scored(journey))
    version_id = state["result"]["result_version_id"]
    appeal = journey.request(
        "POST",
        f"/api/v1/candidate/results/{version_id}/appeals",
        state["candidate"],
        json={
            "category": "SCORING",
            "grounds": "Synthetic pinned revision verification.",
            "operation_id": str(uuid.uuid4()),
        },
    )
    baseline = journey.request(
        "GET", journey.org + f"/appeals/{appeal['id']}/evidence", state["admin"]
    )
    original = baseline["items"][0]
    context = build_tenant_context(
        journey.session, journey.admin_id, uuid.UUID(journey.organisation_id)
    )
    response = journey.session.get(CandidateResponse, uuid.UUID(original["response_id"]))
    assert response is not None and response.current_revision == 2
    savepoint = journey.session.begin_nested()
    try:
        journey.session.execute(
            update(CandidateResponse)
            .where(CandidateResponse.id == response.id)
            .values(
                current_payload={"option_id": "b"},
                current_revision=3,
            )
        )
        journey.session.expire(response)
        assert response.current_revision == 3 and response.current_payload == {"option_id": "b"}
        evidence = ResultGovernanceService.appeal_objective_evidence(
            journey.session, context, uuid.UUID(appeal["id"])
        )
        assert evidence["items"][0]["response_revision_id"] == original["response_revision_id"]
        assert evidence["items"][0]["response_revision_number"] == 2
        assert evidence["items"][0]["response"] == {"option_id": "a"}
        assert evidence["evidence_bundle_checksum"] == baseline["evidence_bundle_checksum"]
    finally:
        savepoint.rollback()
        journey.session.expire(response)
    assert response.current_revision == 2 and response.current_payload == {"option_id": "a"}
    score_id = journey.session.scalar(
        select(ItemScore.id).where(
            ItemScore.scoring_run_id == uuid.UUID(state["run"]["id"]),
            ItemScore.exam_version_item_binding_id == uuid.UUID(original["binding_id"]),
        )
    )
    assert score_id is not None
    for missing in (
        {"candidate_response_revision_id": None},
        {"candidate_response_id": None},
        {"response_checksum": None},
    ):
        partial = journey.session.begin_nested()
        try:
            journey.session.execute(
                update(ItemScore).where(ItemScore.id == score_id).values(**missing)
            )
            journey.session.expire_all()
            with pytest.raises(ResultGovernanceError, match="response evidence is incomplete"):
                ResultGovernanceService.appeal_objective_evidence(
                    journey.session, context, uuid.UUID(appeal["id"])
                )
        finally:
            partial.rollback()
            journey.session.expire_all()


def test_unanswered_objective_evidence_is_explicit_and_safe(journey: Journey):
    admin = journey.login("admin@demo.prufai.local")
    assignment = journey.request(
        "POST",
        journey.org + "/exam-assignments",
        admin,
        expected=201,
        json={
            "candidate_user_id": str(journey.candidate_id),
            "exam_version_id": journey.identifiers["golden_exam_version_id"],
        },
    )
    candidate = journey.login(journey.email)
    attempt = journey.request(
        "POST",
        f"/api/v1/candidate/exam-assignments/{assignment['id']}/attempts",
        candidate,
        expected=201,
    )
    path = f"/api/v1/candidate/exam-attempts/{attempt['id']}"
    journey.request("POST", path + "/start", candidate)
    journey.request("POST", path + "/submit", candidate)
    run = journey.request("POST", journey.org + f"/exam-attempts/{attempt['id']}/score", admin)
    journey.session.expire_all()
    version = journey.session.scalar(
        select(ResultVersion).where(ResultVersion.scoring_run_id == uuid.UUID(run["id"]))
    )
    assert version is not None and version.raw_awarded_points == Decimal("0")
    version_path = journey.org + f"/result-versions/{version.id}"
    journey.request("POST", version_path + "/request-review", admin)
    journey.request("POST", version_path + "/approve", admin)
    journey.request(
        "POST", version_path + "/release", admin, json={"operation_id": str(uuid.uuid4())}
    )
    appeal = journey.request(
        "POST",
        f"/api/v1/candidate/results/{version.id}/appeals",
        candidate,
        json={
            "category": "SCORING",
            "grounds": "Synthetic unanswered-response inspection.",
            "operation_id": str(uuid.uuid4()),
        },
    )
    evidence = journey.request("GET", journey.org + f"/appeals/{appeal['id']}/evidence", admin)
    item = evidence["items"][0]
    assert item["response"] == {}
    assert item["response_id"] is None and item["response_revision_id"] is None
    assert item["response_revision_number"] is None and item["response_checksum"] is None
    assert Decimal(item["awarded_points"]) == Decimal("0")
    assert item["correct_option_ids"] == ["a"]
    assert evidence["matches_pinned_bundle"] is True
