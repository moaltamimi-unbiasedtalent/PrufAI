"""Technical Speaking execution: fresh Alembic PostgreSQL and genuine private S3.

Original generated tones are real validated input, not speech understanding or
linguistic quality evidence. Provider fault adapters are explicitly TEST ONLY.
No positive AIRun, recording, transcript, proposal or review is fabricated.
"""

from __future__ import annotations

import copy
import hashlib
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
from sqlalchemy.orm import Session

from app.ai_orchestration.service import AIOrchestrationService
from app.authorization import build_tenant_context
from app.models import (
    AgentVersion,
    AIModelProfile,
    AIRun,
    AIRunStep,
    AIWorkflowVersion,
    CandidateResponseRevision,
    HumanReviewRequest,
    OrganisationMembership,
    PromptVersion,
    ResultRelease,
    RubricVersion,
    ScoringRun,
    SpeakingExecutionJob,
    SpeechTranscriptionVersion,
    SubjectiveAssessmentVersion,
    User,
)
from app.models.enums import (
    AIArtifactStatus,
    AIRunStatus,
    HumanReviewStatus,
    MembershipStatus,
    SubjectiveAssessmentStatus,
)
from app.scripts.seed_demo import PLATFORM_ADMIN_EMAIL
from app.speaking_execution.execution import SpeakingExecutionService
from app.speaking_execution.provider import ProviderTimeout, ProviderUnavailable
from app.speaking_execution.runtime import build_speaking_provider
from app.writing_assessment.service import (
    SubjectiveAssessmentService,
    canonical_checksum,
)
from tests.alembic_helpers import repository_alembic_head
from tests.integration_support.audio_evidence import technical_encoded_audio, technical_tone_wav
from tests.test_recorded_audio_postgresql_minio_integration import RecordedAudio
from tests.test_recorded_audio_postgresql_minio_integration import audio as recorded_audio_fixture

REQUIRED = (
    os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
    or os.getenv("PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION") == "1"
)
pytestmark = pytest.mark.skipif(
    not (os.getenv("POSTGRES_INTEGRATION_URL") and os.getenv("OBJECT_STORAGE_ENDPOINT"))
    and not REQUIRED,
    reason="AI Speaking requires real PostgreSQL AND private MinIO/S3",
)
API_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class SpeakingProof(RecordedAudio):
    database_url: str

    def start(self, index: int = 0) -> dict[str, Any]:
        assignment = self.call(
            "POST",
            self.org + "/exam-assignments",
            self.login("admin@demo.prufai.local"),
            expected=201,
            json={
                "candidate_user_id": str(self.users[index].id),
                "exam_version_id": self.ids["ai_speaking_exam_version_id"],
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
            i
            for m in runtime["runtime_plan"]["modules"]
            for s in m["sections"]
            for t in s["tasks"]
            for i in t["items"]
        ]
        assert len(items) == 1 and items[0]["response_mode"] == "AUDIO"
        assert items[0]["item_version_id"] == self.ids["ai_speaking_item_version_id"]
        return {"id": attempt["id"], "path": path, "headers": headers, "items": items}

    def submitted(self, *, submit=True, webm=False):
        state = self.start()
        content = technical_encoded_audio(self.settings) if webm else technical_tone_wav()
        asset = self.finalize(
            state,
            self.record(state),
            content=content,
            mime="audio/webm;codecs=opus" if webm else "audio/wav",
        )
        assert (
            asset["status"] == "READY" and asset["validation_provenance"]["complete_input"] is True
        )
        self.attach(state, asset)
        before = self.evidence(state)
        self.call("GET", state["path"], state["headers"])
        after = self.evidence(state)
        assert len(after["recordings"]) == len(before["recordings"]) == 1
        for old, new in zip(before["recordings"], after["recordings"]):
            assert new["recording_session_id"] == old["recording_session_id"]
            assert new["asset"] == old["asset"]
        if submit:
            self.call("POST", state["path"] + "/submit", state["headers"])
        self.session.expire_all()
        revision = self.session.scalar(
            select(CandidateResponseRevision).where(
                CandidateResponseRevision.attempt_id == uuid.UUID(state["id"])
            )
        )
        assert revision is not None
        return {**state, "asset": asset, "revision": revision, "original": content}

    def body(self, state, *, operation=None):
        rubric = self.session.get(
            RubricVersion, uuid.UUID(self.ids["ai_speaking_rubric_version_id"])
        )
        asset = self.asset(state["asset"])
        return {
            "operation_id": operation or str(uuid.uuid4()),
            "attempt_id": state["id"],
            "exam_version_item_binding_id": state["items"][0]["binding_id"],
            "candidate_response_revision_id": str(state["revision"].id),
            "response_checksum": state["revision"].payload_checksum,
            "rubric_version_id": str(rubric.id),
            "rubric_checksum": rubric.checksum,
            "candidate_media_asset_id": str(asset.id),
            "audio_sha256": asset.sha256,
            "duration_ms": asset.duration_ms,
            "recording_policy_checksum": asset.recording_session.evidence_identity[
                "policy_checksum"
            ],
            "assessment_policy_checksum": canonical_checksum(rubric.ai_assessment_policy),
            **{
                key: self.ids["ai_speaking_" + key]
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
        return self.call(
            "POST",
            self.org + "/speaking-executions",
            headers or self.login("admin@demo.prufai.local"),
            expected=expected,
            json=body or self.body(state),
        )

    def job(self, value):
        identifier = value.get("id", value.get("job_id")) if isinstance(value, dict) else value
        with self.factory() as session:
            row = session.get(SpeakingExecutionJob, uuid.UUID(str(identifier)))
            assert row is not None
            session.expunge(row)
            return row

    def claim(self, *, worker="speaking-proof", lease_seconds=90):
        with self.factory() as session:
            job = SpeakingExecutionService.claim_next(
                session, worker_id=worker, lease_seconds=lease_seconds
            )
            session.commit()
            if job is not None:
                session.expunge(job)
            return job

    def execute(self, job, *, provider_factory=None):
        return SpeakingExecutionService.execute_claim(
            self.factory,
            job_id=job.id,
            claim_token=job.claim_token,
            settings=self.settings,
            provider_factory=provider_factory or build_speaking_provider,
        )

    def due(self, job, *, expired=False):
        values = {"next_attempt_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        if expired:
            values["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        with self.factory() as session:
            session.execute(
                update(SpeakingExecutionJob)
                .where(SpeakingExecutionJob.id == job.id)
                .values(**values)
            )
            session.commit()

    def environment(self):
        return {
            **os.environ,
            "DATABASE_URL": self.database_url,
            "ENVIRONMENT": "test",
            "SPEAKING_TEST_ADAPTER_ENABLED": "1",
            "OBJECT_STORAGE_BACKEND": "s3",
            "PYTHONPATH": str(API_ROOT),
        }

    def worker_once(self):
        result = subprocess.run(
            [sys.executable, "-m", "app.speaking_execution.worker", "--once"],
            cwd=API_ROOT,
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=75,
        )
        assert result.returncode == 0, result.stderr
        with self.factory() as session:
            states = [
                (j.status, j.failure_category)
                for j in session.scalars(select(SpeakingExecutionJob))
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
def speaking_execution(monkeypatch):
    """Only our UUID database is dropped; the supplied database is never reset."""
    configured = os.getenv("POSTGRES_INTEGRATION_URL")
    if not configured:
        pytest.fail("Required AI Speaking gate needs POSTGRES_INTEGRATION_URL")
    if not os.getenv("OBJECT_STORAGE_ENDPOINT"):
        pytest.fail("Required AI Speaking gate needs OBJECT_STORAGE_ENDPOINT")
    source = make_url(configured)
    database = "prufai_speaking_execution_" + uuid.uuid4().hex
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
        monkeypatch.setenv("SPEAKING_TEST_ADAPTER_ENABLED", "1")
        generator = recorded_audio_fixture.__wrapped__()
        audio = next(generator)
        audio.settings.speaking_test_adapter_enabled = True
        with audio.factory() as session:
            candidate = session.get(User, uuid.UUID(audio.ids["ai_speaking_candidate_user_id"]))
            session.expunge(candidate)
        audio.users[0] = candidate
        yield SpeakingProof(**audio.__dict__, database_url=fresh)
    finally:
        if generator is not None:
            generator.close()
        with psycopg.connect(control, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


def test_fresh_alembic_private_original_audio_separate_worker_and_pending_review(
    speaking_execution,
):
    p = speaking_execution
    with p.factory() as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == repository_alembic_head()
        )
    state = p.submitted()
    asset = p.asset(state["asset"])
    original = p.storage.get(asset.storage_key)
    assert original == state["original"] == technical_tone_wav()
    assert hashlib.sha256(original).hexdigest() == asset.sha256
    queued = p.job(p.queue(state))
    assert queued.status == "QUEUED" and queued.attempt_count == 0
    assert queued.audio_sha256 == asset.sha256
    assert queued.candidate_media_asset_id == asset.id
    assert queued.candidate_response_revision_id == state["revision"].id
    assert queued.organisation_id == asset.organisation_id
    assert queued.candidate_user_id == asset.candidate_user_id
    assert queued.attempt_id == asset.attempt_id
    assert queued.exam_version_item_binding_id == asset.exam_version_item_binding_id
    assert queued.recording_session_id == asset.recording_session_id
    assert queued.byte_size == len(original) and queued.verified_duration_ms == asset.duration_ms
    assert queued.execution_snapshot["provider"] == "DETERMINISTIC_TEST"
    assert queued.profile_version and queued.snapshot_checksum and queued.input_checksum
    p.worker_once()
    complete = p.job(queued.id)
    assert complete.status == "COMPLETED" and complete.attempt_count == 1
    assert complete.provider_input_checksum and complete.audio_transport
    assert complete.output_checksum == canonical_checksum(complete.validated_proposal)
    with p.factory() as session:
        run = session.get(AIRun, complete.ai_run_id)
        assert run.status is AIRunStatus.SUCCEEDED
        steps = list(session.scalars(select(AIRunStep).where(AIRunStep.run_id == run.id)))
        assert len(steps) == 1 and steps[0].status.value == "SUCCEEDED"
        version = session.get(SubjectiveAssessmentVersion, complete.assessment_version_id)
        assert version.status is SubjectiveAssessmentStatus.AI_PROPOSED
        assert version.approved_score is None and version.proposed_score is not None
        assert len(version.criterion_assessments) == 2
        assert all(c.approved_points is None for c in version.criterion_assessments)
        reviews = list(
            session.scalars(
                select(HumanReviewRequest).where(HumanReviewRequest.ai_run_id == run.id)
            )
        )
        assert len(reviews) == 1 and reviews[0].status is HumanReviewStatus.PENDING
        assert reviews[0].reviewed_checksum == version.checksum
        assert session.scalar(select(func.count()).select_from(SpeechTranscriptionVersion)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(ScoringRun)
                .where(ScoringRun.attempt_id == complete.attempt_id)
            )
            == 0
        )


def _provider_factory(*, scenario=None, mutation=None, delay=0):
    def create(snapshot, settings):
        delegate = build_speaking_provider(snapshot, settings)

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


def test_duplicate_request_and_duplicate_delivery_never_repeat_handoff(speaking_execution):
    p = speaking_execution
    state = p.submitted()
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
    completed = p.job(first.id)
    assert completed.attempt_count == 1
    forged = {**copy.deepcopy(completed.validated_proposal), "rubric_checksum": "f" * 64}
    response = p.client.post(
        p.org + f"/speaking-reviews/{completed.assessment_version_id}/propose",
        headers=p.login("admin@demo.prufai.local"),
        json={"ai_run_id": str(completed.ai_run_id), "proposal": forged},
    )
    assert response.status_code == 409, response.text
    assert p.job(first.id).validated_proposal == completed.validated_proposal


def test_two_independent_postgresql_workers_claim_exclusively_and_fence_expired_token(
    speaking_execution,
):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    barrier, lock, pids = Barrier(2), Lock(), set()

    def claim(index):
        with p.factory() as session:
            pid = session.scalar(text("SELECT pg_backend_pid()"))
            with lock:
                pids.add(pid)
            barrier.wait(timeout=10)
            row = SpeakingExecutionService.claim_next(session, worker_id=f"pg-worker-{index}")
            time.sleep(0.2)
            session.commit()
            return (row.id, row.claim_token) if row else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, range(2)))
    assert len(pids) == 2 and sum(value is not None for value in claimed) == 1
    old = p.job(job.id)
    with p.factory() as session:
        assert SpeakingExecutionService.heartbeat(
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
    speaking_execution, during_provider
):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    script = """
import time
from app.config import Settings
from app.db import create_session_factory
from app.speaking_execution.execution import SpeakingExecutionService as Service
from app.speaking_execution.runtime import build_speaking_provider
settings=Settings();factory=create_session_factory(settings)
with factory() as session:
 job=Service.claim_next(session,worker_id='speaking-process-proof');session.commit()
 identifier,token=job.id,job.claim_token
"""
    if during_provider:
        script += """
def blocking(snapshot,settings):
 delegate=build_speaking_provider(snapshot,settings)
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
def test_transient_provider_failure_is_retryable_and_same_job_recovers(
    speaking_execution, scenario
):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    p.execute(p.claim(), provider_factory=_provider_factory(scenario=scenario))
    assert p.job(job.id).status == "RETRYABLE"
    p.assert_no_handoff(job)
    p.due(job)
    p.execute(p.claim())
    assert p.job(job.id).status == "COMPLETED" and p.job(job.id).attempt_count == 2
    assert len(p.outcomes(job)[1]) == 1


def test_retry_exhaustion_is_terminal_without_false_completed_run(speaking_execution):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    for _ in range(3):
        p.execute(p.claim(), provider_factory=_provider_factory(scenario="timeout"))
        p.due(job)
    current = p.job(job.id)
    assert current.status == "FAILED" and current.attempt_count == 3
    assert p.claim() is None
    p.assert_no_handoff(job)


def test_database_failure_after_provider_response_rolls_back_handoff_then_recovers(
    speaking_execution,
):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    injected = False

    def fail_once(session):
        nonlocal injected
        if not injected and any(
            isinstance(row, SpeakingExecutionJob) and row.id == job.id and row.status == "COMPLETED"
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
def test_authorized_cancellation_fences_late_completion(speaking_execution, running):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    claim = p.claim() if running else None
    cancelled = p.call(
        "POST", p.org + f"/speaking-executions/{job.id}/cancel", p.login("admin@demo.prufai.local")
    )
    assert cancelled["status"] == "CANCELLED"
    if claim:
        assert p.execute(claim) is None
    assert p.claim() is None
    p.assert_no_handoff(job)


def test_running_execution_renews_short_real_lease_and_excludes_another_worker(speaking_execution):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    claim = p.claim(lease_seconds=1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(p.execute, claim, provider_factory=_provider_factory(delay=2.5))
        time.sleep(1.5)
        assert p.claim(worker="must-not-steal-live-work") is None
        assert future.result(timeout=15).status == "COMPLETED"
    assert p.job(job.id).attempt_count == 1


@pytest.mark.parametrize(
    "during_provider", [False, True], ids=["before-provider", "after-provider"]
)
def test_revoked_request_authority_blocks_provider_or_late_handoff(
    speaking_execution, during_provider
):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
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
        delegate = build_speaking_provider(snapshot, settings)

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


def test_duplicate_concurrent_delivery_records_one_authoritative_completion(speaking_execution):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    claim = p.claim()
    barrier = Barrier(2)

    def factory(snapshot, settings):
        delegate = build_speaking_provider(snapshot, settings)

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
def test_explicit_human_rejection_or_changes_never_promotes_proposal(speaking_execution, decision):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    p.worker_once()
    versions, reviews = p.outcomes(job)
    path = p.org + f"/subjective-assessments/{versions[0].id}/decision"
    body = {
        "operation_id": str(uuid.uuid4()),
        "expected_checksum": versions[0].checksum,
        "decision": decision,
        "reason": "Human judgment rejects or requires changes to technical evidence.",
    }
    result = p.call("POST", path, p.login("reviewer@demo.prufai.local"), json=body)
    assert result["status"] != "APPROVED"
    assert p.call("POST", path, p.login("reviewer@demo.prufai.local"), json=body) == result
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
    speaking_execution, artifact, change
):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
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
            .where(record.id == uuid.UUID(p.ids["ai_speaking_" + identifier]))
            .values(**values)
        )
        session.commit()
    called = False

    def must_not_execute(snapshot, settings):
        nonlocal called
        called = True
        return build_speaking_provider(snapshot, settings)

    p.execute(p.claim(), provider_factory=must_not_execute)
    assert not called
    assert p.job(job.id).status == "FAILED"
    assert p.job(job.id).failure_category == "PROFILE_APPROVAL_INVALID"
    p.assert_no_handoff(job)


def test_generic_ai_run_mutators_cannot_bypass_domain_execution_receipt(speaking_execution):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
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
            with pytest.raises(ValueError, match="Speaking|speaking|execution"):
                action()
            session.rollback()
    response = p.client.post(
        f"/api/v1/platform/ai/runs/{job.ai_run_id}/cancel",
        headers=p.login(PLATFORM_ADMIN_EMAIL, tenant=False),
    )
    assert response.status_code == 409
    assert p.job(job.id).status == "RUNNING"
    p.assert_no_handoff(job)


SAFETY_CASES = json.loads(
    (Path(__file__).parent / "fixtures/speaking_execution_safety.json").read_text()
)


def _invalid(proposal, case):
    first = proposal["criteria"][0]
    reference = first["evidence"][0]
    if case == "missing-criterion":
        proposal["criteria"].pop()
    elif case == "unknown-criterion":
        first["criterion_code"] = "NOT_IN_PUBLISHED_RUBRIC"
    elif case == "duplicate-criterion":
        proposal["criteria"][1] = copy.deepcopy(first)
    elif case == "unknown-level":
        first["proposed_level"] = "APPROVED_BY_AI"
    elif case in {"above-maximum", "negative-mark", "nan-mark", "boolean-mark", "precision"}:
        first["proposed_points"] = {
            "above-maximum": "9999",
            "negative-mark": "-1",
            "nan-mark": "NaN",
            "boolean-mark": True,
            "precision": "1.00001",
        }[case]
    elif case == "missing-evidence":
        first["evidence"] = []
    elif case == "outside-span":
        reference["end_ms"] = proposal["duration_ms"] + 1
    elif case == "reversed-span":
        reference["start_ms"], reference["end_ms"] = 1, 0
    elif case == "negative-span":
        reference["start_ms"] = -1
    elif case == "boolean-span":
        reference["start_ms"] = True
    elif case == "fractional-span":
        reference["end_ms"] = 0.5
    elif case == "missing-rationale":
        first["rationale"] = ""
    elif case == "overlong-rationale":
        first["rationale"] = "x" * 2001
    elif case == "wrong-execution":
        proposal["execution_id"] = str(uuid.uuid4())
    elif case == "wrong-run":
        proposal["ai_run_id"] = str(uuid.uuid4())
    elif case == "wrong-response":
        proposal["response_checksum"] = "f" * 64
    elif case == "wrong-response-revision":
        proposal["response_revision_id"] = str(uuid.uuid4())
    elif case == "wrong-rubric":
        proposal["rubric_checksum"] = "f" * 64
    elif case == "wrong-media":
        reference["candidate_media_asset_id"] = str(uuid.uuid4())
    elif case == "wrong-sha":
        reference["audio_sha256"] = "f" * 64
    elif case == "wrong-duration":
        proposal["duration_ms"] += 1
    elif case == "transcript-only":
        first["evidence"] = [
            {
                "type": "TRANSCRIPT",
                "speech_transcription_version_id": str(uuid.uuid4()),
                "quote": "invented transcript cannot prove acoustic performance",
            }
        ]
    elif case == "wrong-modality":
        proposal["evidence_mode"] = "TRANSCRIPT"
    elif case == "authority-escalation":
        proposal["approved"] = True
        proposal["release_result"] = True
    elif case == "malformed-output":
        proposal.clear()
    else:
        raise AssertionError(case)


@pytest.mark.parametrize("case", SAFETY_CASES, ids=lambda case: case["id"])
def test_targeted_technical_output_constraints_reject_without_linguistic_quality_claim(
    speaking_execution, case
):
    assert case["claim_scope"] == "TECHNICAL_SAFETY_ONLY"
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    p.execute(
        p.claim(),
        provider_factory=_provider_factory(
            mutation=lambda output: _invalid(output, case["mutation"])
        ),
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
        ("candidate_media_asset_id", "00000000-0000-0000-0000-000000000001"),
        ("audio_sha256", "f" * 64),
        ("duration_ms", 999999),
        ("recording_policy_checksum", "f" * 64),
        ("assessment_policy_checksum", "f" * 64),
    ],
)
def test_stale_exact_request_pins_create_no_job_run_or_assessment(speaking_execution, field, value):
    p = speaking_execution
    state = p.submitted()
    p.queue(state, body={**p.body(state), field: value}, expected=409)
    with p.factory() as session:
        for model in (SpeakingExecutionJob, AIRun, SubjectiveAssessmentVersion):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_unconfigured_test_boundary_has_no_execution_or_human_fallback(speaking_execution):
    p = speaking_execution
    state = p.submitted()
    p.settings.speaking_test_adapter_enabled = False
    p.queue(state, expected=409)
    with p.factory() as session:
        for model in (SpeakingExecutionJob, AIRun, SubjectiveAssessmentVersion):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_candidate_other_tenant_and_platform_denied_all_institution_execution_surfaces(
    speaking_execution,
):
    p = speaking_execution
    state = p.submitted()
    body = p.body(state)
    actors = [
        state["headers"],
        p.login(p.users[1].email),
        p.login(p.users[2].email, org=str(p.foreign_org)),
        p.login(PLATFORM_ADMIN_EMAIL, tenant=False),
        p.login(PLATFORM_ADMIN_EMAIL),
    ]
    for actor in actors:
        response = p.client.post(p.org + "/speaking-executions", headers=actor, json=body)
        assert response.status_code in {400, 403, 404}, response.text
    with p.factory() as session:
        assert session.scalar(select(func.count()).select_from(SpeakingExecutionJob)) == 0
    job = p.job(p.queue(state, body=body))
    for actor in actors:
        for suffix in ("", "/" + str(job.id), "/options"):
            response = p.client.get(p.org + "/speaking-executions" + suffix, headers=actor)
            assert response.status_code in {400, 403, 404}, response.text
        for action in ("retry", "cancel"):
            response = p.client.post(
                p.org + f"/speaking-executions/{job.id}/{action}", headers=actor
            )
            assert response.status_code in {400, 403, 404}, response.text
    assert p.job(job.id).status == "QUEUED"


def test_client_cannot_supply_private_audio_keys_credentials_prompts_or_completed_run(
    speaking_execution,
):
    p = speaking_execution
    state = p.submitted()
    for field in (
        "ai_run_id",
        "storage_key",
        "private_url",
        "audio",
        "api_key",
        "system_template",
        "approved",
        "speech_transcription_version_id",
    ):
        p.queue(
            state, body={**p.body(state), field: "TEST_ONLY_UNTRUSTED_CLIENT_FIELD"}, expected=422
        )
    with p.factory() as session:
        assert session.scalar(select(func.count()).select_from(SpeakingExecutionJob)) == 0


@pytest.mark.parametrize("state_name", ["queued", "running", "cancelled", "failed"])
def test_existing_speaking_proposal_endpoint_rejects_fabricated_or_noncompleted_run(
    speaking_execution, state_name
):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    if state_name == "running":
        p.claim()
    elif state_name == "cancelled":
        p.call(
            "POST",
            p.org + f"/speaking-executions/{job.id}/cancel",
            p.login("admin@demo.prufai.local"),
        )
    elif state_name == "failed":
        p.execute(
            p.claim(),
            provider_factory=_provider_factory(
                mutation=lambda output: _invalid(output, "missing-criterion")
            ),
        )
    for identifier in (str(job.ai_run_id), str(uuid.uuid4())):
        response = p.client.post(
            p.org + f"/speaking-reviews/{job.assessment_version_id}/propose",
            headers=p.login("admin@demo.prufai.local"),
            json={"ai_run_id": identifier, "proposal": {}},
        )
        assert response.status_code in {403, 409}, response.text
        p.assert_no_handoff(job)


def test_independent_human_explicit_complete_decisions_create_immutable_successor(
    speaking_execution,
):
    p = speaking_execution
    state = p.submitted()
    job = p.job(p.queue(state))
    p.worker_once()
    versions, reviews = p.outcomes(job)
    version, review = versions[0], reviews[0]
    body = {
        "operation_id": str(uuid.uuid4()),
        "expected_checksum": version.checksum,
        "reason": "Independent human judgment after original audio playback.",
        "corrections": {
            code: {
                "approved_points": "1",
                "approved_level": "DEVELOPING",
                "rationale": "Independent human technical judgment after private playback.",
                "evidence": [
                    {
                        "type": "AUDIO",
                        "candidate_media_asset_id": state["asset"]["media_asset_id"],
                        "audio_sha256": state["asset"]["sha256"],
                        "start_ms": 0,
                        "end_ms": state["asset"]["duration_ms"],
                    }
                ],
            }
            for code in ("TASK", "LANGUAGE")
        },
    }
    path = p.org + f"/subjective-assessments/{version.id}/approve"
    for actor in (
        p.login("admin@demo.prufai.local"),
        state["headers"],
        p.login(p.users[1].email),
        p.login(p.users[2].email, org=str(p.foreign_org)),
        p.login(PLATFORM_ADMIN_EMAIL, tenant=False),
    ):
        response = p.client.post(path, headers=actor, json=body)
        assert response.status_code in {400, 403, 404, 409}, response.text
    reviewer = p.login("reviewer@demo.prufai.local")
    for change in (
        {"expected_checksum": "f" * 64},
        {"corrections": {}},
        {"reason": ""},
        {"corrections": {"TASK": body["corrections"]["TASK"]}},
        {"operation_id": None},
    ):
        response = p.client.post(path, headers=reviewer, json={**body, **change})
        assert response.status_code in {403, 409}, response.text
    for field, invalid in (("rationale", ""), ("evidence", [])):
        corrections = copy.deepcopy(body["corrections"])
        corrections["TASK"][field] = invalid
        response = p.client.post(path, headers=reviewer, json={**body, "corrections": corrections})
        assert response.status_code == 409, response.text
    corrections = copy.deepcopy(body["corrections"])
    corrections["TASK"]["evidence"][0]["start_ms"] = True
    response = p.client.post(path, headers=reviewer, json={**body, "corrections": corrections})
    assert response.status_code == 409, response.text
    for action in ("approve", "reject", "request-changes"):
        response = p.client.post(
            f"/api/v1/platform/ai/reviews/{review.id}/{action}",
            headers=p.login(PLATFORM_ADMIN_EMAIL, tenant=False),
        )
        assert response.status_code == 409
    with p.factory() as session:
        actor = session.scalar(select(User).where(User.email == "reviewer@demo.prufai.local"))
        context = build_tenant_context(session, actor.id, job.organisation_id)
        with pytest.raises(ValueError, match="GOVERNED_SPEAKING_REVIEW_REQUIRED"):
            SubjectiveAssessmentService.correct(
                session,
                context,
                session.get(SubjectiveAssessmentVersion, version.id),
                body["corrections"],
                session.get(HumanReviewRequest, review.id),
            )
        session.rollback()
    approved = p.call("POST", path, reviewer, json=body)
    repeated = p.call("POST", path, reviewer, json=body)
    assert repeated["id"] == approved["id"]
    assert repeated["status"] == approved["status"] == "APPROVED"
    assert Decimal(repeated["approved_score"]) == Decimal(approved["approved_score"]) == 2
    response = p.client.post(path, headers=reviewer, json={**body, "reason": "Changed replay"})
    assert response.status_code == 409
    with p.factory() as session:
        original = session.get(SubjectiveAssessmentVersion, version.id)
        successor = session.get(SubjectiveAssessmentVersion, uuid.UUID(approved["id"]))
        assert original.status is SubjectiveAssessmentStatus.SUPERSEDED
        assert original.approved_score is None and original.ai_proposal == version.ai_proposal
        assert successor.supersedes_version_id == original.id and successor.approved_score == 2
        for criterion in successor.criterion_assessments:
            decision = body["corrections"][criterion.criterion_code]
            assert criterion.approved_points == Decimal(decision["approved_points"])
            assert criterion.rationale == decision["rationale"]
            assert criterion.evidence == decision["evidence"]
        assert session.get(HumanReviewRequest, review.id).status is HumanReviewStatus.APPROVED
        assert (
            session.scalar(
                select(func.count())
                .select_from(ScoringRun)
                .where(ScoringRun.attempt_id == job.attempt_id)
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(ResultRelease)) == 0
        actor = session.scalar(select(User).where(User.email == "reviewer@demo.prufai.local"))
        context = build_tenant_context(session, actor.id, job.organisation_id)
        approved_id = successor.id
        approved_checksum = successor.checksum
        with pytest.raises(ValueError, match="GOVERNED_SPEAKING_REVIEW_REQUIRED"):
            SubjectiveAssessmentService.correct(
                session,
                context,
                successor,
                body["corrections"],
                session.get(HumanReviewRequest, review.id),
            )
        session.rollback()
        assert (
            session.scalar(
                select(func.count())
                .select_from(SubjectiveAssessmentVersion)
                .where(
                    SubjectiveAssessmentVersion.subjective_assessment_id
                    == version.subjective_assessment_id
                )
            )
            == 2
        )
        successor = session.get(SubjectiveAssessmentVersion, approved_id)
        assert successor.approved_score == 2
        original = session.get(SubjectiveAssessmentVersion, version.id)
        original.provenance = {"rewritten": True}
        with pytest.raises(ValueError, match="immutable|cannot"):
            session.commit()
        session.rollback()
        successor.provenance = {"rewritten": True}
        with pytest.raises(ValueError, match="immutable|cannot"):
            session.commit()
        session.rollback()

    # An actual generic workflow request is an adversarial review envelope,
    # never a provider execution or fabricated successful Speaking result.
    platform = p.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    generic = p.call(
        "POST",
        f"/api/v1/platform/ai/workflow-versions/{job.execution_profile_id}/run",
        platform,
        expected=202,
        json={"request_id": "shadow-review-negative-" + uuid.uuid4().hex},
    )
    for target in (approved["id"], str(version.subjective_assessment_id)):
        response = p.client.post(
            f"/api/v1/platform/ai/runs/{generic['id']}/review",
            headers=platform,
            json={
                "entity_type": "UNTRUSTED_ALIAS",
                "entity_id": target,
                "checksum": approved_checksum,
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "review target requires a domain governed decision"
    with p.factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(HumanReviewRequest)
                .where(
                    HumanReviewRequest.entity_id.in_(
                        [uuid.UUID(approved["id"]), version.subjective_assessment_id]
                    )
                )
            )
            == 0
        )
        assert (
            session.get(SubjectiveAssessmentVersion, uuid.UUID(approved["id"])).approved_score == 2
        )
        assert (
            session.get(SubjectiveAssessmentVersion, version.id).ai_proposal == version.ai_proposal
        )


@pytest.mark.parametrize("boundary", ["before-provider", "after-provider", "missing-object"])
def test_real_private_object_tampering_never_creates_successful_handoff(
    speaking_execution, boundary
):
    p = speaking_execution
    state = p.submitted()
    job = p.job(p.queue(state))
    asset = p.asset(state["asset"])
    called = False

    def tamper():
        if boundary == "missing-object":
            p.storage.client.delete_object(Bucket=p.storage.bucket, Key=asset.storage_key)
        else:
            replacement = technical_tone_wav(frequency_hz=880)
            assert len(replacement) == len(state["original"]) and replacement != state["original"]
            p.storage.put(asset.storage_key, replacement, mime_type=asset.content_type)

    def factory(snapshot, settings):
        nonlocal called
        called = True
        delegate = build_speaking_provider(snapshot, settings)

        class Adapter:
            provider, model_name, adapter_version = (
                delegate.provider,
                delegate.model_name,
                delegate.adapter_version,
            )

            def generate(self, input, **kwargs):
                result = delegate.generate(input, **kwargs)
                tamper()
                return result

        return Adapter()

    if boundary != "after-provider":
        tamper()
    p.execute(p.claim(), provider_factory=factory)
    assert called is (boundary == "after-provider")
    assert p.job(job.id).status in {"FAILED", "RETRYABLE"}
    p.assert_no_handoff(job)


def test_real_webm_original_is_preserved_while_audio_transport_is_separately_pinned(
    speaking_execution,
):
    p = speaking_execution
    state = p.submitted(webm=True)
    job = p.job(p.queue(state))
    p.worker_once()
    completed = p.job(job.id)
    assert completed.audio_sha256 == hashlib.sha256(state["original"]).hexdigest()
    assert completed.audio_transport["source_sha256"] == completed.audio_sha256
    assert completed.audio_transport["sha256"] != completed.audio_sha256
    assert completed.audio_transport["format"] == "wav"
    assert completed.audio_transport["transform_version"] == "PCM16_PRESERVE_TIMELINE_V1"
    assert p.storage.get(p.asset(state["asset"]).storage_key) == state["original"]


def test_institution_private_playback_proposal_and_control_centre_safe_metadata(speaking_execution):
    p = speaking_execution
    state = p.submitted()
    job = p.job(p.queue(state))
    p.worker_once()
    version = p.outcomes(job)[0][0]
    path = p.org + f"/speaking-reviews/{version.subjective_assessment_id}"
    result = p.call("GET", path, p.login("reviewer@demo.prufai.local"))
    assert result["status"] == "AI_PROPOSED" and result["audio_sha256"] == state["asset"]["sha256"]
    assert result["original_audio_primary_evidence"] is True
    assert result["derived_transcription"] is False
    for actor in (
        state["headers"],
        p.login(p.users[1].email),
        p.login(p.users[2].email, org=str(p.foreign_org)),
        p.login(PLATFORM_ADMIN_EMAIL, tenant=False),
        p.login(PLATFORM_ADMIN_EMAIL),
    ):
        denied = p.client.get(path, headers=actor)
        assert denied.status_code in {400, 403, 404}, denied.text
    platform = p.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    operations = p.call(
        "GET", "/api/v1/platform/control-center/ai-operations?kind=speaking-proposals", platform
    )
    audit = p.call(
        "GET",
        "/api/v1/platform/control-center/audit?event_type=speaking_execution.completed",
        platform,
    )
    encoded = json.dumps([operations, audit])
    assert str(job.id) in encoded
    row = next(item for item in operations["items"] if item["id"] == str(job.id))
    assert row["test_only"] is True and row["provider"] == "DETERMINISTIC_TEST"
    assert row["status"] == "COMPLETED" and row["human_review_status"] == "PENDING"
    assert row["approval_status"] == "APPROVED"
    assert p.asset(state["asset"]).storage_key not in encoded
    assert "base64" not in encoded.lower()
    for private in (
        "candidate_text",
        "validated_proposal",
        "audio_sha256",
        "candidate_media_asset_id",
        "system_template",
        "instruction_template",
        "credential_reference",
        "raw_provider_payload",
        "private_url",
    ):
        assert private not in encoded
    with p.factory() as session:
        from app.models import AuditEvent

        events = list(
            session.scalars(
                select(AuditEvent).where(AuditEvent.event_type.like("speaking_execution.%"))
            )
        )
        metadata = json.dumps([row.metadata_json for row in events])
        assert p.asset(state["asset"]).storage_key not in metadata
        assert "instruction_template" not in metadata and "validated_proposal" not in metadata


@pytest.mark.parametrize(
    "policy_case", ["HUMAN_ONLY", "TRANSCRIPT", "AUDIO_AND_TRANSCRIPT", "TRANSCRIPT_CRITERION"]
)
def test_unsupported_modes_or_nonacoustic_criterion_never_queue_or_change_mode(
    speaking_execution, policy_case
):
    p = speaking_execution
    state = p.submitted()
    rubric_id = uuid.UUID(p.ids["ai_speaking_rubric_version_id"])
    with p.factory() as session:
        rubric = session.get(RubricVersion, rubric_id)
        policy, criteria = (
            copy.deepcopy(rubric.ai_assessment_policy),
            copy.deepcopy(rubric.criteria),
        )
        if policy_case == "HUMAN_ONLY":
            policy.update(enabled=False, mode="HUMAN_ONLY")
        elif policy_case == "TRANSCRIPT_CRITERION":
            criteria[0]["evidence_mode"] = "TRANSCRIPT"
        else:
            policy["evidence_mode"] = policy_case
        # Controlled SQL corruption in this owned fresh DB tests fail-closed
        # consumption; published production policy cannot be edited this way.
        session.execute(
            update(RubricVersion)
            .where(RubricVersion.id == rubric_id)
            .values(ai_assessment_policy=policy, criteria=criteria)
        )
        session.commit()
    p.session.expire_all()
    p.queue(state, expected=409)
    with p.factory() as session:
        for model in (
            SpeakingExecutionJob,
            AIRun,
            SubjectiveAssessmentVersion,
            SpeechTranscriptionVersion,
        ):
            assert session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("change", ["sha", "duration", "validation"])
def test_postqueue_media_metadata_drift_stops_before_provider(speaking_execution, change):
    p = speaking_execution
    state = p.submitted()
    job = p.job(p.queue(state))
    from app.models import CandidateMediaAsset

    values = {
        "sha": {"sha256": "f" * 64},
        "duration": {"duration_ms": 999999},
        "validation": {"validation_provenance": {"complete_input": False}},
    }[change]
    with p.factory() as session:
        session.execute(
            update(CandidateMediaAsset)
            .where(CandidateMediaAsset.id == job.candidate_media_asset_id)
            .values(**values)
        )
        session.commit()
    called = False

    def never_called(snapshot, settings):
        nonlocal called
        called = True
        return build_speaking_provider(snapshot, settings)

    p.execute(p.claim(), provider_factory=never_called)
    assert called is False and p.job(job.id).status == "FAILED"
    p.assert_no_handoff(job)


def test_completed_execution_run_steps_and_ai_criterion_history_cannot_be_rewritten(
    speaking_execution,
):
    p = speaking_execution
    job = p.job(p.queue(p.submitted()))
    p.worker_once()
    with p.factory() as session:
        completed = session.get(SpeakingExecutionJob, job.id)
        completed.validated_proposal = {"rewritten": True}
        with pytest.raises(ValueError, match="immutable|cannot"):
            session.commit()
        session.rollback()
        completed = session.get(SpeakingExecutionJob, job.id)
        completed.audio_transport = {"rewritten": True}
        with pytest.raises(ValueError, match="immutable|cannot"):
            session.commit()
        session.rollback()
        run = session.get(AIRun, job.ai_run_id)
        run.request_id = "rewritten-run-history"
        with pytest.raises(ValueError, match="immutable|cannot"):
            session.commit()
        session.rollback()
        step = session.scalar(select(AIRunStep).where(AIRunStep.run_id == job.ai_run_id))
        step.output_checksum = "f" * 64
        with pytest.raises(ValueError, match="immutable|cannot"):
            session.commit()
        session.rollback()
        version = session.get(SubjectiveAssessmentVersion, job.assessment_version_id)
        version.criterion_assessments[0].rationale = "rewritten-private-judgment"
        with pytest.raises(ValueError, match="immutable|cannot"):
            session.commit()
        session.rollback()
        session.delete(session.get(SpeakingExecutionJob, job.id))
        with pytest.raises(ValueError, match="history|cannot"):
            session.commit()
        session.rollback()
    assert p.job(job.id).status == "COMPLETED"
    assert len(p.outcomes(job)[0]) == len(p.outcomes(job)[1]) == 1
