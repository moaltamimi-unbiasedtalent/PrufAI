"""Required durable Writing execution proof on fresh Alembic PostgreSQL.

Every positive AI run/proposal is produced by the actual separate worker or
execution service through an explicitly TEST ONLY provider. This proves technical
execution/governance, never genuine inference or German scoring accuracy.
"""

from __future__ import annotations

import copy
import json
import os
import selectors
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Lock
from typing import Any

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import event, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.ai_orchestration.service import AIOrchestrationService
from app.authorization import build_tenant_context
from app.config import get_settings
from app.main import app
from app.models import (
    AgentVersion,
    AIModelProfile,
    AIRun,
    AIRunStep,
    AIWorkflowVersion,
    AuditEvent,
    CandidateResponseRevision,
    HumanReviewRequest,
    OrganisationMembership,
    PromptVersion,
    ResultRelease,
    RubricEvidenceBinding,
    RubricVersion,
    ScoringRun,
    SubjectiveAssessmentVersion,
    User,
    WritingExecutionJob,
)
from app.models.enums import (
    AIArtifactStatus,
    AIRunStatus,
    MembershipStatus,
    SubjectiveAssessmentStatus,
)
from app.scripts.seed_ai_writing_demo import AI_WRITING_CANDIDATE_EMAIL
from app.scripts.seed_demo import PLATFORM_ADMIN_EMAIL
from app.writing_assessment.service import (
    RubricService,
    SubjectiveAssessmentService,
    canonical_checksum,
)
from app.writing_execution.execution import WritingExecutionService
from app.writing_execution.provider import ProviderTimeout, ProviderUnavailable
from app.writing_execution.runtime import build_writing_provider
from tests.alembic_helpers import repository_alembic_head
from tests.test_writing_foundation_postgresql_integration import (
    WRITING_RESPONSE,
    WritingJourney,
)
from tests.test_writing_foundation_postgresql_integration import writing as writing_fixture

REQUIRED = os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_INTEGRATION_URL") and not REQUIRED,
    reason="Durable Writing execution requires real Alembic PostgreSQL",
)
API_ROOT = Path(__file__).resolve().parents[1]
SAFETY_CASES = json.loads(
    (Path(__file__).parent / "fixtures/writing_execution_safety.json").read_text()
)


@dataclass
class WritingExecutionProof(WritingJourney):
    factory: Any = None
    settings: Any = None
    database_url: str = ""

    def submit(self, *, candidate_text: str = WRITING_RESPONSE) -> dict[str, Any]:
        administrator = self.login("admin@demo.prufai.local")
        assignment = self.request(
            "POST",
            self.org + "/exam-assignments",
            administrator,
            expected=201,
            json={
                "candidate_user_id": str(self.candidate_id),
                "exam_version_id": self.exam_version_id,
            },
        )
        candidate = self.login(self.email)
        attempt = self.request(
            "POST",
            f"/api/v1/candidate/exam-assignments/{assignment['id']}/attempts",
            candidate,
            expected=201,
        )
        path = f"/api/v1/candidate/exam-attempts/{attempt['id']}"
        runtime = self.request("POST", path + "/start", candidate)
        item = runtime["runtime_plan"]["modules"][0]["sections"][0]["tasks"][0]["items"][0]
        assert (
            item["response_mode"] == "LONG_TEXT" and item["item_version_id"] == self.item_version_id
        )
        self.request(
            "PUT",
            path + f"/responses/{item['binding_id']}",
            candidate,
            json={
                "client_operation_id": str(uuid.uuid4()),
                "expected_revision": 0,
                "payload": {"text": candidate_text},
            },
        )
        self.request("POST", path + "/submit", candidate)
        self.session.expire_all()
        revision = self.session.scalar(
            select(CandidateResponseRevision).where(
                CandidateResponseRevision.attempt_id == uuid.UUID(attempt["id"])
            )
        )
        assert revision is not None
        return {
            "attempt": attempt,
            "item": item,
            "revision": revision,
            "text": candidate_text,
            "candidate": candidate,
            "path": path,
        }

    def body(self, state, *, operation=None):
        rubric = self.session.get(
            RubricVersion, uuid.UUID(self.identifiers["ai_writing_rubric_version_id"])
        )
        return {
            "operation_id": operation or str(uuid.uuid4()),
            "attempt_id": state["attempt"]["id"],
            "exam_version_item_binding_id": state["item"]["binding_id"],
            "candidate_response_revision_id": str(state["revision"].id),
            "response_checksum": state["revision"].payload_checksum,
            "rubric_version_id": str(rubric.id),
            "rubric_checksum": rubric.checksum,
            **{
                key: self.identifiers["ai_writing_" + key]
                for key in (
                    "execution_profile_id",
                    "execution_profile_version",
                    "model_profile_id",
                    "prompt_version_id",
                    "agent_version_id",
                    "workflow_version_id",
                )
            },
        }

    def queue(self, state, *, body=None, headers=None, expected=201):
        return self.request(
            "POST",
            self.org + "/writing-executions",
            headers or self.login("admin@demo.prufai.local"),
            expected=expected,
            json=body or self.body(state),
        )

    def job(self, value):
        identifier = value.get("id", value.get("job_id")) if isinstance(value, dict) else value
        with self.factory() as session:
            record = session.get(WritingExecutionJob, uuid.UUID(str(identifier)))
            assert record is not None
            session.expunge(record)
            return record

    def claim(self, *, worker="writing-test-worker", lease_seconds=90):
        with self.factory() as session:
            job = WritingExecutionService.claim_next(
                session, worker_id=worker, lease_seconds=lease_seconds
            )
            session.commit()
            if job is not None:
                session.expunge(job)
            return job

    def execute(self, job, *, provider_factory=None):
        return WritingExecutionService.execute_claim(
            self.factory,
            job_id=job.id,
            claim_token=job.claim_token,
            settings=self.settings,
            provider_factory=provider_factory or build_writing_provider,
        )

    def due(self, job, *, expired=False):
        values = {"next_attempt_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        if expired:
            values["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        with self.factory() as session:
            session.execute(
                update(WritingExecutionJob).where(WritingExecutionJob.id == job.id).values(**values)
            )
            session.commit()

    def environment(self):
        return {
            **os.environ,
            "DATABASE_URL": self.database_url,
            "ENVIRONMENT": "test",
            "WRITING_TEST_ADAPTER_ENABLED": "1",
            "PYTHONPATH": str(API_ROOT),
        }

    def worker_once(self):
        result = subprocess.run(
            [sys.executable, "-m", "app.writing_execution.worker", "--once"],
            cwd=API_ROOT,
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        with self.factory() as session:
            states = [
                (job.status, job.failure_category)
                for job in session.scalars(select(WritingExecutionJob))
            ]
        assert "COMPLETED" in result.stdout, (result.stdout, states)
        return result

    def outcomes(self, job):
        with self.factory() as session:
            versions = list(
                session.scalars(
                    select(SubjectiveAssessmentVersion).where(
                        SubjectiveAssessmentVersion.ai_run_id == job.ai_run_id
                    )
                )
            )
            reviews = list(
                session.scalars(
                    select(HumanReviewRequest).where(HumanReviewRequest.ai_run_id == job.ai_run_id)
                )
            )
            return versions, reviews

    def assert_no_handoff(self, job):
        with self.factory() as session:
            version = session.get(SubjectiveAssessmentVersion, job.assessment_version_id)
            assert version.status != SubjectiveAssessmentStatus.AI_PROPOSED
            assert version.proposed_score is None and version.approved_score is None
            assert version.criterion_assessments == []
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(HumanReviewRequest)
                    .where(HumanReviewRequest.ai_run_id == job.ai_run_id)
                )
                == 0
            )
            assert session.get(AIRun, job.ai_run_id).status != AIRunStatus.SUCCEEDED


@pytest.fixture
def writing_execution(monkeypatch):
    configured = os.getenv("POSTGRES_INTEGRATION_URL")
    if not configured:
        pytest.fail("Required Writing execution gate needs POSTGRES_INTEGRATION_URL")
    source = make_url(configured)
    database = "prufai_writing_execution_" + uuid.uuid4().hex
    fresh = source.set(database=database).render_as_string(hide_password=False)
    control = source.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    with psycopg.connect(control, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    generator = None
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_ROOT,
            env={**os.environ, "DATABASE_URL": fresh},
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr
        monkeypatch.setenv("POSTGRES_INTEGRATION_URL", fresh)
        monkeypatch.setenv("WRITING_TEST_ADAPTER_ENABLED", "1")
        generator = writing_fixture.__wrapped__()
        journey = next(generator)
        settings = app.dependency_overrides[get_settings]()
        settings.writing_test_adapter_enabled = True
        yield WritingExecutionProof(
            **{
                **journey.__dict__,
                "email": AI_WRITING_CANDIDATE_EMAIL,
                "candidate_id": uuid.UUID(journey.identifiers["ai_writing_candidate_user_id"]),
                "exam_version_id": journey.identifiers["ai_writing_exam_version_id"],
                "item_version_id": journey.identifiers["ai_writing_item_version_id"],
            },
            factory=sessionmaker(bind=journey.session.get_bind(), expire_on_commit=False),
            settings=settings,
            database_url=fresh,
        )
    finally:
        if generator is not None:
            generator.close()
        with psycopg.connect(control, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


def _provider_factory(*, scenario=None, mutation=None, delay=0):
    def create(snapshot, settings):
        delegate = build_writing_provider(snapshot, settings)

        class TechnicalProvider:
            provider, model_name, adapter_version = (
                delegate.provider,
                delegate.model_name,
                delegate.adapter_version,
            )

            def generate(self, input, **kwargs):
                if delay:
                    time.sleep(delay)
                if scenario == "timeout":
                    raise ProviderTimeout("TEST ONLY timeout")
                if scenario == "unavailable":
                    raise ProviderUnavailable("TEST ONLY unavailable")
                result = delegate.generate(input, **kwargs)
                if mutation is not None:
                    proposal = copy.deepcopy(result.proposal)
                    mutation(proposal)
                    result = replace(result, proposal=proposal)
                return result

        return TechnicalProvider()

    return create


def test_fresh_alembic_separate_worker_legitimate_proposal_and_pending_human_review(
    writing_execution,
):
    p = writing_execution
    assert (
        p.session.scalar(text("SELECT version_num FROM alembic_version"))
        == repository_alembic_head()
    )
    state = p.submit()
    body = p.body(state)
    queued = p.job(p.queue(state, body=body))
    assert queued.status == "QUEUED" and queued.attempt_count == 0
    assert queued.candidate_response_revision_id == state["revision"].id
    assert queued.response_checksum == state["revision"].payload_checksum
    assert queued.candidate_user_id == p.candidate_id
    assert queued.organisation_id == uuid.UUID(p.identifiers["organisation_id"])
    assert queued.exam_version_id == uuid.UUID(p.exam_version_id)
    assert queued.question_bank_item_version_id == uuid.UUID(p.item_version_id)
    assert queued.profile_version == body["execution_profile_version"]
    assert queued.execution_snapshot["model_version"]
    assert queued.execution_snapshot["prompt_checksum"]
    assert queued.execution_snapshot["workflow_checksum"]
    assert queued.execution_snapshot["approval"]
    p.assert_no_handoff(queued)
    result = p.worker_once()
    completed = p.job(queued.id)
    assert completed.status == "COMPLETED" and "COMPLETED" in result.stdout
    assert completed.output_checksum and completed.provider_request_id.startswith("test-only-")
    assert [s["status"] for s in completed.state_history] == ["QUEUED", "RUNNING", "COMPLETED"]
    with p.factory() as session:
        run = session.get(AIRun, completed.ai_run_id)
        assert run.status is AIRunStatus.SUCCEEDED and run.completed_at is not None
        assert str(run.workflow_version_id) == body["workflow_version_id"]
        assert str(run.agent_version_id) == body["agent_version_id"]
        steps = list(session.scalars(select(AIRunStep).where(AIRunStep.run_id == run.id)))
        assert len(steps) == 1 and steps[0].status is AIRunStatus.SUCCEEDED
        assert steps[0].input_checksum == completed.input_checksum
        assert steps[0].output_checksum == completed.output_checksum
        assert steps[0].provider_request_id == completed.provider_request_id
        version = session.get(SubjectiveAssessmentVersion, completed.assessment_version_id)
        assert version.status is SubjectiveAssessmentStatus.AI_PROPOSED
        assert version.assessment_origin == "AI" and version.approved_score is None
        assert (
            version.ai_run_id == run.id and version.response_checksum == completed.response_checksum
        )
        assert version.rubric_checksum == completed.rubric_checksum
        assert len(version.criterion_assessments) == 2
        assert all("TEST ONLY" in c.rationale for c in version.criterion_assessments)
        reviews = list(
            session.scalars(
                select(HumanReviewRequest).where(HumanReviewRequest.ai_run_id == run.id)
            )
        )
        assert len(reviews) == 1 and reviews[0].status.value == "PENDING"
        assert reviews[0].reviewed_checksum == version.checksum
        assert reviews[0].decided_by is reviews[0].decided_at is None
        assert (
            session.get(
                CandidateResponseRevision, completed.candidate_response_revision_id
            ).payload["text"]
            == state["text"]
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ScoringRun)
                .where(ScoringRun.attempt_id == completed.attempt_id)
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(ResultRelease)) == 0


def test_duplicate_request_and_duplicate_delivery_never_repeat_handoff(writing_execution):
    p = writing_execution
    state = p.submit()
    body = p.body(state)
    first = p.job(p.queue(state, body=body))
    assert p.queue(state, body=body)["id"] == str(first.id)
    conflicting = {**body, "response_checksum": "f" * 64}
    p.queue(state, body=conflicting, expected=409)
    claim = p.claim()
    p.execute(claim)
    assert p.execute(claim) is None
    assert p.queue(state, body=body)["id"] == str(first.id)
    versions, reviews = p.outcomes(first)
    assert len(versions) == len(reviews) == 1
    assert p.job(first.id).attempt_count == 1


def test_two_independent_postgresql_workers_claim_exclusively_and_fence_expired_token(
    writing_execution,
):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    barrier, lock, pids = Barrier(2), Lock(), set()

    def claim(index):
        with p.factory() as session:
            pid = session.scalar(text("SELECT pg_backend_pid()"))
            with lock:
                pids.add(pid)
            barrier.wait(timeout=10)
            row = WritingExecutionService.claim_next(session, worker_id=f"pg-worker-{index}")
            time.sleep(0.2)
            session.commit()
            return (row.id, row.claim_token) if row else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, range(2)))
    assert len(pids) == 2 and sum(value is not None for value in claimed) == 1
    old = p.job(job.id)
    with p.factory() as session:
        assert WritingExecutionService.heartbeat(
            session, job_id=old.id, claim_token=old.claim_token
        )
        session.commit()
    p.due(old, expired=True)
    new = p.claim(worker="recovered")
    assert new.claim_token != old.claim_token
    assert p.execute(old) is None
    p.execute(new)
    assert p.job(job.id).status == "COMPLETED"
    assert len(p.outcomes(job)[0]) == 1


def _marker(process, expected):
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        assert selector.select(timeout=30), "worker did not reach the controlled boundary"
        assert process.stdout.readline().strip() == expected
    finally:
        selector.close()


@pytest.mark.parametrize("during_provider", [False, True], ids=["after-claim", "during-provider"])
def test_actual_worker_process_kill_and_restart_recovers_without_duplicate_proposal(
    writing_execution, during_provider
):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    script = """
import time
from app.config import Settings
from app.db import create_session_factory
from app.writing_execution.execution import WritingExecutionService as Service
from app.writing_execution.runtime import build_writing_provider
settings=Settings();factory=create_session_factory(settings)
with factory() as session:
 job=Service.claim_next(session,worker_id='writing-process-proof');session.commit()
 identifier,token=job.id,job.claim_token
"""
    if during_provider:
        script += """
def blocking(snapshot,settings):
 delegate=build_writing_provider(snapshot,settings)
 class Adapter:
  provider,model_name,adapter_version=delegate.provider,delegate.model_name,delegate.adapter_version
  def generate(self,input,**kwargs):
   print('PROVIDER_ENTERED',flush=True);time.sleep(60)
   return delegate.generate(input,**kwargs)
 return Adapter()
Service.execute_claim(factory,job_id=identifier,claim_token=token,settings=settings,provider_factory=blocking)
"""
        expected = "PROVIDER_ENTERED"
    else:
        script += "print('CLAIM_COMMITTED',flush=True)\ntime.sleep(60)\n"
        expected = "CLAIM_COMMITTED"
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script],
        cwd=API_ROOT,
        env=p.environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _marker(process, expected)
        p.assert_no_handoff(job)
        assert p.job(job.id).status == "RUNNING"
        process.kill()
        process.wait(timeout=10)
        p.due(job, expired=True)
        p.worker_once()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    assert p.job(job.id).status == "COMPLETED" and p.job(job.id).attempt_count == 2
    versions, reviews = p.outcomes(job)
    assert len(versions) == len(reviews) == 1


@pytest.mark.parametrize("scenario", ["timeout", "unavailable"])
def test_transient_provider_failure_is_retryable_and_same_job_recovers(writing_execution, scenario):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    p.execute(p.claim(), provider_factory=_provider_factory(scenario=scenario))
    assert p.job(job.id).status == "RETRYABLE"
    p.assert_no_handoff(job)
    p.due(job)
    p.execute(p.claim())
    assert p.job(job.id).status == "COMPLETED" and p.job(job.id).attempt_count == 2
    assert len(p.outcomes(job)[1]) == 1


def test_retry_exhaustion_is_terminal_without_false_completed_run(writing_execution):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    for _ in range(3):
        p.execute(p.claim(), provider_factory=_provider_factory(scenario="timeout"))
        p.due(job)
    current = p.job(job.id)
    assert current.status == "FAILED" and current.attempt_count == 3
    assert p.claim() is None
    p.assert_no_handoff(job)


def test_database_failure_after_provider_response_rolls_back_handoff_then_recovers(
    writing_execution,
):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    injected = False

    def fail_once(session):
        nonlocal injected
        if not injected and any(
            isinstance(row, WritingExecutionJob) and row.id == job.id and row.status == "COMPLETED"
            for row in session.identity_map.values()
        ):
            injected = True
            raise OperationalError("TEST ONLY completion failure", {}, RuntimeError("injected"))

    event.listen(Session, "before_commit", fail_once)
    try:
        p.execute(p.claim())
    finally:
        event.remove(Session, "before_commit", fail_once)
    assert injected and p.job(job.id).status == "RETRYABLE"
    p.assert_no_handoff(job)
    p.due(job)
    p.execute(p.claim())
    assert p.job(job.id).status == "COMPLETED"
    assert len(p.outcomes(job)[0]) == len(p.outcomes(job)[1]) == 1


@pytest.mark.parametrize("running", [False, True], ids=["queued", "running"])
def test_authorized_cancellation_fences_late_completion(writing_execution, running):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    claim = p.claim() if running else None
    cancelled = p.request(
        "POST", p.org + f"/writing-executions/{job.id}/cancel", p.login("admin@demo.prufai.local")
    )
    assert cancelled["status"] == "CANCELLED"
    if claim:
        assert p.execute(claim) is None
    assert p.claim() is None
    p.assert_no_handoff(job)


def test_running_execution_renews_short_real_lease_and_excludes_another_worker(writing_execution):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    claim = p.claim(lease_seconds=1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(p.execute, claim, provider_factory=_provider_factory(delay=2.5))
        time.sleep(1.5)
        assert p.claim(worker="must-not-steal-live-work") is None
        assert future.result(timeout=15).status == "COMPLETED"
    assert p.job(job.id).attempt_count == 1


def _invalid(proposal, case):
    first = proposal["criteria"][0]
    if case == "missing-criterion":
        proposal["criteria"].pop()
    elif case == "unknown-criterion":
        first["criterion_code"] = "NOT_IN_PUBLISHED_RUBRIC"
    elif case == "duplicate-criterion":
        proposal["criteria"][1] = copy.deepcopy(first)
    elif case == "unknown-level":
        first["proposed_level"] = "APPROVED_BY_AI"
    elif case == "above-maximum":
        first["proposed_points"] = "9999"
    elif case == "negative-mark":
        first["proposed_points"] = "-1"
    elif case == "nan-mark":
        first["proposed_points"] = "NaN"
    elif case == "boolean-mark":
        first["proposed_points"] = True
    elif case == "missing-evidence":
        first["evidence"] = []
    elif case == "outside-evidence":
        first["evidence"] = [{"start": 0, "end": 999999, "quote": "not real"}]
    elif case == "wrong-quote":
        first["evidence"][0]["quote"] = "NOT THE SUBMITTED CANDIDATE TEXT"
    elif case == "boolean-offset":
        first["evidence"][0]["start"] = True
    elif case == "missing-rationale":
        first["rationale"] = ""
    elif case == "wrong-execution":
        proposal["execution_id"] = str(uuid.uuid4())
    elif case == "wrong-run":
        proposal["ai_run_id"] = str(uuid.uuid4())
    elif case == "wrong-response":
        proposal["response_checksum"] = "f" * 64
    elif case == "wrong-rubric":
        proposal["rubric_checksum"] = "f" * 64
    elif case == "authority-escalation":
        proposal["approved"] = True
    elif case == "invalid-confidence":
        first["confidence"] = "CALIBRATED_CERTAINTY"
    else:
        raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "missing-criterion",
        "unknown-criterion",
        "duplicate-criterion",
        "unknown-level",
        "above-maximum",
        "negative-mark",
        "nan-mark",
        "boolean-mark",
        "missing-evidence",
        "outside-evidence",
        "wrong-quote",
        "boolean-offset",
        "missing-rationale",
        "wrong-execution",
        "wrong-run",
        "wrong-response",
        "wrong-rubric",
        "authority-escalation",
        "invalid-confidence",
    ],
)
def test_invalid_provider_output_is_terminal_before_completed_run_or_proposal(
    writing_execution, case
):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    p.execute(
        p.claim(),
        provider_factory=_provider_factory(mutation=lambda output: _invalid(output, case)),
    )
    assert p.job(job.id).status == "FAILED"
    p.assert_no_handoff(job)


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_response_revision_id", "00000000-0000-0000-0000-000000000001"),
        ("response_checksum", "f" * 64),
        ("rubric_checksum", "f" * 64),
        ("execution_profile_version", "f" * 64),
        ("workflow_version_id", "00000000-0000-0000-0000-000000000001"),
        ("model_profile_id", "00000000-0000-0000-0000-000000000001"),
        ("prompt_version_id", "00000000-0000-0000-0000-000000000001"),
        ("agent_version_id", "00000000-0000-0000-0000-000000000001"),
    ],
)
def test_stale_exact_request_identity_never_creates_job_or_ai_run(writing_execution, field, value):
    p = writing_execution
    state = p.submit()
    body = {**p.body(state), field: value}
    p.queue(state, body=body, expected=409)
    with p.factory() as session:
        assert session.scalar(select(func.count()).select_from(WritingExecutionJob)) == 0
        assert session.scalar(select(func.count()).select_from(AIRun)) == 0
        assert session.scalar(select(func.count()).select_from(SubjectiveAssessmentVersion)) == 0


def test_unconfigured_test_boundary_creates_no_fake_execution_or_human_fallback(writing_execution):
    p = writing_execution
    state = p.submit()
    p.settings.writing_test_adapter_enabled = False
    p.queue(state, expected=409)
    with p.factory() as session:
        assert session.scalar(select(func.count()).select_from(WritingExecutionJob)) == 0
        assert session.scalar(select(func.count()).select_from(AIRun)) == 0
        assert session.scalar(select(func.count()).select_from(SubjectiveAssessmentVersion)) == 0


def test_candidate_same_org_other_foreign_and_platform_are_denied_institution_execution(
    writing_execution,
):
    p = writing_execution
    state = p.submit()
    body = p.body(state)
    actors = [
        state["candidate"],
        p.login(p.other_email),
        p.login(p.foreign_email, organisation_id=str(p.foreign_org_id)),
        p.login(PLATFORM_ADMIN_EMAIL, tenant=False),
        p.login(PLATFORM_ADMIN_EMAIL),
    ]
    for actor in actors:
        response = p.client.post(p.org + "/writing-executions", headers=actor, json=body)
        assert response.status_code in {400, 403, 404}, response.text
    with p.factory() as session:
        assert session.scalar(select(func.count()).select_from(WritingExecutionJob)) == 0
        assert session.scalar(select(func.count()).select_from(AIRun)) == 0


def test_extra_client_credentials_private_inputs_or_ai_run_ids_are_rejected(writing_execution):
    p = writing_execution
    state = p.submit()
    for field in ("ai_run_id", "candidate_text", "api_key", "system_template", "approved"):
        body = {**p.body(state), field: "TEST_ONLY_UNTRUSTED_CLIENT_FIELD"}
        p.queue(state, body=body, expected=422)
    with p.factory() as session:
        assert session.scalar(select(func.count()).select_from(WritingExecutionJob)) == 0


@pytest.mark.parametrize("state_name", ["queued", "running", "cancelled", "failed"])
def test_public_propose_rejects_noncompleted_actual_execution_and_fabricated_run(
    writing_execution, state_name
):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    if state_name == "running":
        p.claim()
    elif state_name == "cancelled":
        p.request(
            "POST",
            p.org + f"/writing-executions/{job.id}/cancel",
            p.login("admin@demo.prufai.local"),
        )
    elif state_name == "failed":
        p.execute(
            p.claim(),
            provider_factory=_provider_factory(
                mutation=lambda output: _invalid(output, "missing-criterion")
            ),
        )
    path = p.org + f"/subjective-assessments/{job.assessment_version_id}/propose"
    for identifier in (str(job.ai_run_id), str(uuid.uuid4())):
        response = p.client.post(
            path,
            headers=p.login("admin@demo.prufai.local"),
            json={"ai_run_id": identifier, "criteria": []},
        )
        assert response.status_code in {403, 409}, response.text
        p.assert_no_handoff(job)


def test_human_review_requires_independence_exact_checksum_and_explicit_criterion_judgment(
    writing_execution,
):
    p = writing_execution
    state = p.submit()
    job = p.job(p.queue(state))
    p.worker_once()
    version, review = p.outcomes(job)[0][0], p.outcomes(job)[1][0]
    path = p.org + f"/subjective-assessments/{version.id}/approve"
    body = {
        "operation_id": str(uuid.uuid4()),
        "expected_checksum": version.checksum,
        "reason": "Explicit human technical review; criterion values independently entered.",
        "corrections": {
            code: {"approved_points": "1", "approved_level": "DEVELOPING"}
            for code in ("TASK", "LANGUAGE")
        },
    }
    administrator = p.login("admin@demo.prufai.local")
    reviewer = p.login("reviewer@demo.prufai.local")
    for actor in (
        administrator,
        state["candidate"],
        p.login(p.other_email),
        p.login(p.foreign_email, organisation_id=str(p.foreign_org_id)),
        p.login(PLATFORM_ADMIN_EMAIL, tenant=False),
    ):
        response = p.client.post(path, headers=actor, json=body)
        assert response.status_code in {400, 403, 404, 409}, response.text
    for change in ({"expected_checksum": "f" * 64}, {"corrections": {}}, {"reason": ""}):
        response = p.client.post(path, headers=reviewer, json={**body, **change})
        assert response.status_code in {403, 409}, response.text
    for action in ("approve", "reject", "request-changes"):
        response = p.client.post(
            f"/api/v1/platform/ai/reviews/{review.id}/{action}",
            headers=p.login(PLATFORM_ADMIN_EMAIL, tenant=False),
        )
        assert response.status_code == 409
    with p.factory() as session:
        actor = session.scalar(select(User).where(User.email == "reviewer@demo.prufai.local"))
        context = build_tenant_context(session, actor.id, job.organisation_id)
        with pytest.raises(ValueError, match="GOVERNED_WRITING_REVIEW_REQUIRED"):
            SubjectiveAssessmentService.correct(
                session,
                context,
                session.get(SubjectiveAssessmentVersion, version.id),
                body["corrections"],
                session.get(HumanReviewRequest, review.id),
            )
        session.rollback()
        assert session.scalar(select(func.count()).select_from(SubjectiveAssessmentVersion)) == 1
    approved = p.request("POST", path, reviewer, json=body)
    assert approved["status"] == "APPROVED"
    repeated = p.request("POST", path, reviewer, json=body)
    assert repeated["id"] == approved["id"] and repeated["status"] == approved["status"]
    assert Decimal(repeated["approved_score"]) == Decimal(approved["approved_score"]) == 2
    with p.factory() as session:
        original = session.get(SubjectiveAssessmentVersion, version.id)
        assert original.status is SubjectiveAssessmentStatus.SUPERSEDED
        assert original.approved_score is None and original.proposed_score == 0
        successor = session.get(
            SubjectiveAssessmentVersion, uuid.UUID(approved.get("id", approved.get("version_id")))
        )
        assert successor.id != original.id and successor.supersedes_version_id == original.id
        assert successor.approved_score == 2
        assert session.get(HumanReviewRequest, review.id).status.value == "APPROVED"
        assert (
            session.scalar(
                select(func.count())
                .select_from(ScoringRun)
                .where(ScoringRun.attempt_id == job.attempt_id)
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(ResultRelease)) == 0
        successor.provenance = {"rewritten": True}
        with pytest.raises(ValueError, match="immutable|cannot"):
            session.commit()
        session.rollback()


def test_private_proposal_is_only_in_authorized_institution_views_not_platform_metadata(
    writing_execution,
):
    p = writing_execution
    state = p.submit(candidate_text=WRITING_RESPONSE + " PRIVATE_WRITING_RESPONSE_MARKER")
    job = p.job(p.queue(state))
    p.worker_once()
    versions, _ = p.outcomes(job)
    path = p.org + f"/subjective-assessments/{versions[0].subjective_assessment_id}"
    response = p.client.get(path, headers=p.login("reviewer@demo.prufai.local"))
    assert response.status_code == 200 and "AI_PROPOSED" in response.text
    for actor in (
        state["candidate"],
        p.login(p.other_email),
        p.login(p.foreign_email, organisation_id=str(p.foreign_org_id)),
        p.login(PLATFORM_ADMIN_EMAIL, tenant=False),
        p.login(PLATFORM_ADMIN_EMAIL),
    ):
        denied = p.client.get(path, headers=actor)
        assert denied.status_code in {400, 403, 404}, denied.text
    platform = p.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    operations = p.request(
        "GET", "/api/v1/platform/control-center/ai-operations?kind=writing-proposals", platform
    )
    audit = p.request(
        "GET",
        "/api/v1/platform/control-center/audit?event_type=writing_execution.completed",
        platform,
    )
    encoded = json.dumps([operations, audit])
    assert str(job.id) in encoded
    assert state["text"] not in encoded and "PRIVATE_WRITING_RESPONSE_MARKER" not in encoded
    for private in (
        "candidate_text",
        "validated_proposal",
        "system_template",
        "instruction_template",
        "credential_reference",
    ):
        assert private not in encoded
    with p.factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.event_type.like("writing_execution.%"))
            )
        )
        encoded = json.dumps([row.metadata_json for row in events])
        assert state["text"] not in encoded and "PRIVATE_WRITING_RESPONSE_MARKER" not in encoded


@pytest.mark.parametrize("case", SAFETY_CASES, ids=lambda case: case["id"])
def test_targeted_synthetic_safety_cases_are_constraints_not_linguistic_quality(
    writing_execution, case
):
    p = writing_execution
    assert case["claim_scope"] == "TECHNICAL_SAFETY_ONLY"
    state = p.submit(candidate_text=case["candidate_text"])
    if case["mutation"] == "unsupported_mode":
        # An explicit policy contradiction is simulated only in this isolated DB.
        with p.factory() as session:
            policy = dict(
                session.get(
                    RubricVersion, uuid.UUID(p.identifiers["ai_writing_rubric_version_id"])
                ).ai_assessment_policy
            )
            policy["mode"] = "HUMAN_ONLY"
            session.execute(
                update(RubricVersion)
                .where(RubricVersion.id == uuid.UUID(p.identifiers["ai_writing_rubric_version_id"]))
                .values(ai_assessment_policy=policy)
            )
            session.commit()
        p.queue(state, expected=409)
        with p.factory() as session:
            assert session.scalar(select(func.count()).select_from(AIRun)) == 0
        return
    job = p.job(p.queue(state))
    mutation = {
        "above_maximum": "above-maximum",
        "missing_evidence": "missing-evidence",
        "authority_escalation": "authority-escalation",
        "wrong_rubric": "wrong-rubric",
    }[case["mutation"]]
    p.execute(
        p.claim(),
        provider_factory=_provider_factory(mutation=lambda output: _invalid(output, mutation)),
    )
    assert p.job(job.id).status == "FAILED"
    p.assert_no_handoff(job)


@pytest.mark.parametrize(
    "during_provider", [False, True], ids=["before-provider", "after-provider"]
)
def test_response_corruption_is_detected_before_provider_or_atomic_handoff(
    writing_execution, during_provider
):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    called = False

    def corrupt():
        with p.factory() as session:
            session.execute(
                update(CandidateResponseRevision)
                .where(CandidateResponseRevision.id == job.candidate_response_revision_id)
                .values(payload_checksum="f" * 64)
            )
            session.commit()

    def factory(snapshot, settings):
        nonlocal called
        called = True
        delegate = build_writing_provider(snapshot, settings)

        class Adapter:
            provider, model_name, adapter_version = (
                delegate.provider,
                delegate.model_name,
                delegate.adapter_version,
            )

            def generate(self, input, **kwargs):
                result = delegate.generate(input, **kwargs)
                corrupt()
                return result

        return Adapter()

    if not during_provider:
        corrupt()
    p.execute(p.claim(), provider_factory=factory)
    assert called is during_provider
    assert p.job(job.id).status == "FAILED"
    assert p.job(job.id).failure_category == "STALE_WRITING_EVIDENCE"
    p.assert_no_handoff(job)


@pytest.mark.parametrize("source_requirement", ["binding", "policy"])
def test_required_source_backed_evidence_is_explicitly_unconfigured_without_fabricated_retrieval(
    writing_execution, source_requirement
):
    p = writing_execution
    state = p.submit()
    rubric_id = uuid.UUID(p.identifiers["ai_writing_rubric_version_id"])
    # Only this isolated fixture is corrupted to exercise unsupported source
    # policies. Real published rubrics remain immutable through governance.
    with p.factory() as session:
        if source_requirement == "binding":
            session.execute(
                update(RubricEvidenceBinding)
                .where(RubricEvidenceBinding.rubric_version_id == rubric_id)
                .values(required=True)
            )
            session.expire_all()
            rubric = session.get(RubricVersion, rubric_id)
            checksum = canonical_checksum(RubricService.checksum_payload(rubric))
            session.execute(
                update(RubricVersion).where(RubricVersion.id == rubric_id).values(checksum=checksum)
            )
        else:
            rubric = session.get(RubricVersion, rubric_id)
            policy = {**rubric.evidence_policy, "source_backed": True}
            payload = RubricService.checksum_payload(rubric)
            payload["evidence_policy"] = policy
            checksum = canonical_checksum(payload)
            session.execute(
                update(RubricVersion)
                .where(RubricVersion.id == rubric_id)
                .values(evidence_policy=policy, checksum=checksum)
            )
        session.commit()
    p.session.expire_all()
    response = p.client.post(
        p.org + "/writing-executions",
        headers=p.login("admin@demo.prufai.local"),
        json=p.body(state),
    )
    assert response.status_code == 409 and "SOURCE_BACKED_WRITING_UNCONFIGURED" in response.text
    with p.factory() as session:
        assert session.scalar(select(func.count()).select_from(WritingExecutionJob)) == 0
        assert session.scalar(select(func.count()).select_from(AIRun)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(HumanReviewRequest)
                .where(HumanReviewRequest.ai_run_id.is_not(None))
            )
            == 0
        )


@pytest.mark.parametrize(
    "during_provider", [False, True], ids=["before-provider", "after-provider"]
)
def test_revoked_request_authority_blocks_provider_or_late_handoff(
    writing_execution, during_provider
):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    called = False

    def revoke():
        with p.factory() as session:
            session.execute(
                update(OrganisationMembership)
                .where(OrganisationMembership.id == job.requested_membership_id)
                .values(status=MembershipStatus.SUSPENDED)
            )
            session.commit()

    def factory(snapshot, settings):
        nonlocal called
        called = True
        delegate = build_writing_provider(snapshot, settings)

        class Adapter:
            provider, model_name, adapter_version = (
                delegate.provider,
                delegate.model_name,
                delegate.adapter_version,
            )

            def generate(self, input, **kwargs):
                result = delegate.generate(input, **kwargs)
                revoke()
                return result

        return Adapter()

    if not during_provider:
        revoke()
    p.execute(p.claim(), provider_factory=factory)
    assert called is during_provider
    assert p.job(job.id).status == "FAILED"
    assert p.job(job.id).failure_category == "REQUEST_AUTHORITY_REVOKED"
    p.assert_no_handoff(job)


def test_duplicate_concurrent_delivery_records_one_authoritative_completion(writing_execution):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    claim = p.claim()
    barrier = Barrier(2)

    def factory(snapshot, settings):
        delegate = build_writing_provider(snapshot, settings)

        class Adapter:
            provider, model_name, adapter_version = (
                delegate.provider,
                delegate.model_name,
                delegate.adapter_version,
            )

            def generate(self, input, **kwargs):
                result = delegate.generate(input, **kwargs)
                barrier.wait(timeout=15)
                return result

        return Adapter()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(p.execute, claim, provider_factory=factory) for _ in range(2)]
        outcomes = [future.result(timeout=30) for future in futures]
    assert sum(result is not None for result in outcomes) == 1
    assert p.job(job.id).status == "COMPLETED" and p.job(job.id).attempt_count == 1
    versions, reviews = p.outcomes(job)
    assert len(versions) == len(reviews) == 1


@pytest.mark.parametrize("decision", ["REJECTED", "CHANGES_REQUESTED"])
def test_explicit_human_rejection_or_changes_never_promotes_proposal(writing_execution, decision):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    p.worker_once()
    versions, reviews = p.outcomes(job)
    path = p.org + f"/subjective-assessments/{versions[0].id}/decision"
    body = {
        "operation_id": str(uuid.uuid4()),
        "expected_checksum": versions[0].checksum,
        "decision": decision,
        "reason": "Human judgment rejects or requires changes to technical evidence.",
    }
    result = p.request("POST", path, p.login("reviewer@demo.prufai.local"), json=body)
    assert result["status"] != "APPROVED"
    assert p.request("POST", path, p.login("reviewer@demo.prufai.local"), json=body) == result
    with p.factory() as session:
        assert session.get(HumanReviewRequest, reviews[0].id).status.value == decision
        original = session.get(SubjectiveAssessmentVersion, versions[0].id)
        assert original.approved_score is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(SubjectiveAssessmentVersion)
                .where(SubjectiveAssessmentVersion.status == SubjectiveAssessmentStatus.APPROVED)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ScoringRun)
                .where(ScoringRun.attempt_id == job.attempt_id)
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(ResultRelease)) == 0


@pytest.mark.parametrize("artifact", ["model", "prompt", "agent", "workflow"])
@pytest.mark.parametrize("change", ["revoked", "content"])
def test_postqueue_registry_revocation_or_content_drift_blocks_provider_and_handoff(
    writing_execution, artifact, change
):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    record, identifier, values = {
        "model": (AIModelProfile, "model_profile_id", {"model_name": "different-model"}),
        "prompt": (PromptVersion, "prompt_version_id", {"instruction_template": "DIFFERENT"}),
        "agent": (AgentVersion, "agent_version_id", {"tool_policy": ["release_result"]}),
        "workflow": (AIWorkflowVersion, "workflow_version_id", {"graph_definition": {"nodes": []}}),
    }[artifact]
    if change == "revoked":
        values = {"status": AIArtifactStatus.DISABLED}
    # Direct SQL is only a controlled corruption of an isolated test DB. Normal
    # approved artifact updates are denied by the registry's immutable boundary.
    with p.factory() as session:
        session.execute(
            update(record)
            .where(record.id == uuid.UUID(p.identifiers["ai_writing_" + identifier]))
            .values(**values)
        )
        session.commit()
    called = False

    def must_not_execute(snapshot, settings):
        nonlocal called
        called = True
        return build_writing_provider(snapshot, settings)

    p.execute(p.claim(), provider_factory=must_not_execute)
    assert not called
    assert p.job(job.id).status == "FAILED"
    assert p.job(job.id).failure_category == "PROFILE_APPROVAL_INVALID"
    p.assert_no_handoff(job)


def test_generic_ai_run_mutators_cannot_bypass_domain_execution_receipt(writing_execution):
    p = writing_execution
    job = p.job(p.queue(p.submit()))
    p.claim()
    with p.factory() as session:
        run = session.get(AIRun, job.ai_run_id)
        workflow = session.get(AIWorkflowVersion, run.workflow_version_id)
        actions = [
            lambda: AIOrchestrationService.complete_run(session, run),
            lambda: AIOrchestrationService.fail_run(session, run, "CLIENT_FORGED_FAILURE"),
            lambda: AIOrchestrationService.cancel_run(session, run),
            lambda: AIOrchestrationService.save_checkpoint(
                session, run, workflow, node_id="client", state={"approved": True}
            ),
            lambda: AIOrchestrationService.load_checkpoint(session, run),
            lambda: AIOrchestrationService.request_review(
                session, run, entity_type="FAKE", entity_id=job.assessment_version_id
            ),
        ]
        for action in actions:
            with pytest.raises(ValueError, match="Writing|writing|execution"):
                action()
            session.rollback()
    response = p.client.post(
        f"/api/v1/platform/ai/runs/{job.ai_run_id}/cancel",
        headers=p.login(PLATFORM_ADMIN_EMAIL, tenant=False),
    )
    assert response.status_code == 409
    assert p.job(job.id).status == "RUNNING"
    p.assert_no_handoff(job)
