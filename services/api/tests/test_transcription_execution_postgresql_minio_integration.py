"""Durable technical execution against fresh Alembic PostgreSQL and private S3.

The generated original tone is actual validated recording input. All providers
in these tests are explicitly TEST ONLY: success proves execution and recovery,
never speech recognition, German accuracy or external-provider availability.
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
from pathlib import Path
from threading import Barrier, Event, Lock
from typing import Any

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import event, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.auth.service import AuthenticationService
from app.knowledge.storage import StorageError
from app.models import (
    AIModelProfile,
    AuditEvent,
    CandidateAudioRecordingSession,
    CandidateMediaAsset,
    CandidateResponseRevision,
    OrganisationMembership,
    PlatformRoleAssignment,
    Role,
    SpeechTranscriptionJob,
    SpeechTranscriptionVersion,
    User,
)
from app.models.enums import AIArtifactStatus, CandidateMediaStatus, MembershipStatus, RoleScope
from app.scripts.seed_demo import PASSWORD, PLATFORM_ADMIN_EMAIL
from app.services.identity import IdentityService
from app.speech_transcription.execution import TranscriptionExecutionService, snapshot_profile
from app.speech_transcription.provider import TranscriptionSegment
from app.speech_transcription.runtime import (
    CREDENTIAL_REFERENCE,
    ENDPOINT,
    PROCESSING_REFERENCES,
    build_transcription_provider,
    validate_profile_configuration,
)
from tests.alembic_helpers import repository_alembic_head
from tests.integration_support.audio_evidence import technical_tone_wav
from tests.test_recorded_audio_postgresql_minio_integration import RecordedAudio
from tests.test_recorded_audio_postgresql_minio_integration import audio as recorded_audio_fixture

REQUIRED = (
    os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
    or os.getenv("PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION") == "1"
)
pytestmark = pytest.mark.skipif(
    not (os.getenv("POSTGRES_INTEGRATION_URL") and os.getenv("OBJECT_STORAGE_ENDPOINT"))
    and not REQUIRED,
    reason="Durable transcription requires real PostgreSQL AND private MinIO/S3",
)
API_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ExecutionProof(RecordedAudio):
    database_url: str

    def start(self, index: int = 0) -> dict[str, Any]:
        assignment = self.call(
            "POST",
            self.org + "/exam-assignments",
            self.login("admin@demo.prufai.local"),
            expected=201,
            json={
                "candidate_user_id": str(self.users[index].id),
                "exam_version_id": self.ids["transcription_execution_exam_version_id"],
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
        return {"id": attempt["id"], "path": path, "headers": headers, "items": items}

    def submitted(self, *, submit: bool = True):
        state = self.start()
        asset = self.finalize(state, self.record(state))
        assert asset["status"] == "READY"
        assert asset["validation_provenance"]["complete_input"] is True
        assert asset["duration_ms"] == 1000
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
        return {**state, "asset": asset, "revision": revision}

    def request(self, state, *, operation=None, headers=None, body=None, expected=201):
        return self.call(
            "POST",
            self.org + f"/candidate-media/{state['asset']['media_asset_id']}/transcriptions",
            headers or self.login("admin@demo.prufai.local"),
            expected=expected,
            json=body
            or {
                "operation_id": operation or str(uuid.uuid4()),
                "profile_id": self.ids["transcription_execution_profile_id"],
                "language": "de",
                "candidate_response_revision_id": str(state["revision"].id),
                "audio_sha256": state["asset"]["sha256"],
            },
        )

    def job(self, value):
        identifier = value.get("job_id", value.get("id")) if isinstance(value, dict) else value
        with self.factory() as session:
            row = session.get(SpeechTranscriptionJob, uuid.UUID(str(identifier)))
            assert row is not None
            session.expunge(row)
            return row

    def versions(self):
        with self.factory() as session:
            return list(
                session.scalars(
                    select(SpeechTranscriptionVersion).order_by(
                        SpeechTranscriptionVersion.version_number
                    )
                )
            )

    def profile(self, *, scenario="success", delay=0):
        with self.factory() as session:
            profile = session.get(
                AIModelProfile, uuid.UUID(self.ids["transcription_execution_profile_id"])
            )
            configuration = copy.deepcopy(profile.configuration)
            configuration.update(scenario=scenario, delay_seconds=delay)
            profile.configuration = configuration
            session.commit()

    def claim(self, *, worker="technical-test-worker", lease_seconds=90):
        with self.factory() as session:
            job = TranscriptionExecutionService.claim_next(
                session, worker_id=worker, lease_seconds=lease_seconds
            )
            session.commit()
            if job:
                session.expunge(job)
            return job

    def execute(self, job, *, storage=None, provider_factory=None):
        return TranscriptionExecutionService.execute_claim(
            self.factory,
            job_id=job.id,
            claim_token=job.claim_token,
            storage=storage or self.storage,
            provider_factory=provider_factory or build_transcription_provider,
            settings=self.settings,
        )

    def due(self, job, *, expired_lease=False):
        values = {"next_attempt_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        if expired_lease:
            values["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        with self.factory() as session:
            session.execute(
                update(SpeechTranscriptionJob)
                .where(SpeechTranscriptionJob.id == job.id)
                .values(**values)
            )
            session.commit()

    def environment(self):
        return {
            **os.environ,
            "DATABASE_URL": self.database_url,
            "ENVIRONMENT": "test",
            "SPEECH_TRANSCRIPTION_TEST_ADAPTER_ENABLED": "1",
            "OBJECT_STORAGE_BACKEND": "s3",
            "PYTHONPATH": str(API_ROOT),
        }

    def worker_once(self):
        result = subprocess.run(
            [sys.executable, "-m", "app.speech_transcription.worker", "--once"],
            cwd=API_ROOT,
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=50,
        )
        assert result.returncode == 0, result.stderr
        return result


@pytest.fixture
def execution(monkeypatch):
    """Allocate/drop only our UUID DB; supplied development DB is never reset."""
    configured = os.getenv("POSTGRES_INTEGRATION_URL")
    if not configured:
        pytest.fail("Required durable execution gate needs POSTGRES_INTEGRATION_URL")
    source = make_url(configured)
    database = "prufai_transcription_execution_" + uuid.uuid4().hex
    fresh = source.set(database=database).render_as_string(hide_password=False)
    control = source.set(drivername="postgresql").render_as_string(hide_password=False)
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
        monkeypatch.setenv("SPEECH_TRANSCRIPTION_TEST_ADAPTER_ENABLED", "1")
        generator = recorded_audio_fixture.__wrapped__()
        audio = next(generator)
        audio.settings.speech_transcription_test_adapter_enabled = True
        yield ExecutionProof(**audio.__dict__, database_url=fresh)
    finally:
        if generator is not None:
            generator.close()
        with psycopg.connect(control, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
            )


def test_fresh_alembic_private_original_audio_durable_request_and_separate_cli_completion(
    execution,
):
    with execution.factory() as session:
        assert (
            session.scalar(text("SELECT version_num FROM alembic_version"))
            == repository_alembic_head()
        )
    state = execution.submitted()
    asset = execution.asset(state["asset"])
    original = execution.storage.get(asset.storage_key)
    assert original == technical_tone_wav()
    assert hashlib.sha256(original).hexdigest() == asset.sha256
    queued = execution.job(execution.request(state))
    assert queued.status == "QUEUED" and queued.attempt_count == 0
    assert queued.audio_sha256 == asset.sha256
    assert queued.candidate_media_asset_id == asset.id
    assert queued.candidate_response_revision_id == state["revision"].id
    assert queued.organisation_id == asset.organisation_id
    assert queued.candidate_user_id == asset.candidate_user_id
    assert queued.attempt_id == asset.attempt_id
    assert queued.exam_version_item_binding_id == asset.exam_version_item_binding_id
    assert queued.recording_session_id == asset.recording_session_id
    assert queued.byte_size == len(original) and queued.verified_duration_ms == 1000
    assert queued.source_identity["item_version_id"] == state["items"][0]["item_version_id"]
    assert (
        queued.source_identity["item_version_checksum"]
        == state["items"][0]["item_version_checksum"]
    )
    assert queued.source_identity["policy_checksum"]
    assert (
        queued.source_identity["recording_policy"] == state["items"][0]["content"]["audio_policy"]
    )
    assert queued.profile_snapshot["provider"] == "DETERMINISTIC_TEST"
    assert queued.profile_snapshot["model_name"] == "fixture-transcriber"
    assert queued.profile_snapshot["adapter_version"] == "1"
    assert queued.snapshot_checksum and queued.configuration_checksum
    assert queued.profile_version
    assert execution.versions() == []
    execution.worker_once()
    completed = execution.job(queued.id)
    assert completed.status == "READY" and completed.attempt_count == 1
    assert [entry["status"] for entry in completed.state_history] == ["QUEUED", "RUNNING", "READY"]
    version = execution.versions()[0]
    assert version.execution_job_id == queued.id
    assert version.audio_sha256 == asset.sha256 and version.candidate_media_asset_id == asset.id
    assert version.profile_version == queued.profile_version
    assert "TEST ONLY" in version.transcript
    assert version.segments == []  # Technical tones supply no recognized speech or invented spans.
    assert version.provenance["test_only"] is True
    assert execution.storage.get(asset.storage_key) == original
    assert execution.asset(state["asset"]).status is CandidateMediaStatus.READY


def test_duplicate_request_and_duplicate_worker_delivery_publish_one_version(execution):
    state = execution.submitted()
    operation = str(uuid.uuid4())
    first = execution.job(execution.request(state, operation=operation))
    replay = execution.job(execution.request(state, operation=operation))
    assert first.id == replay.id
    claim = execution.claim()
    execution.execute(claim)
    execution.execute(claim)
    execution.worker_once()
    assert len(execution.versions()) == 1
    assert execution.job(first.id).status == "READY"


def test_two_actual_postgresql_workers_claim_exclusively_and_heartbeat_fences_expired_claim(
    execution,
):
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    barrier = Barrier(2, timeout=10)
    lock = Lock()
    pids = set()
    engine = execution.session.get_bind()

    def overlap(connection, _cursor, statement, _parameters, _context, _many):
        if "speech_transcription_jobs" not in statement or "SKIP LOCKED" not in statement:
            return
        pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        with lock:
            if pid in pids:
                return
            pids.add(pid)
        barrier.wait()

    event.listen(engine, "before_cursor_execute", overlap)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(execution.claim, worker=f"parallel-{index}") for index in range(2)
            ]
            claims = [future.result(timeout=30) for future in futures]
    finally:
        event.remove(engine, "before_cursor_execute", overlap)
    assert len(pids) == 2
    claim = next(value for value in claims if value is not None)
    assert sum(value is not None for value in claims) == 1
    with execution.factory() as session:
        assert TranscriptionExecutionService.heartbeat(
            session, job_id=claim.id, claim_token=claim.claim_token
        )
        session.commit()
    assert execution.claim() is None
    execution.due(claim, expired_lease=True)
    replacement = execution.claim(worker="recovered")
    assert replacement.id == queued.id and replacement.claim_token != claim.claim_token
    assert execution.execute(claim) is None
    execution.execute(replacement)
    assert len(execution.versions()) == 1


def _subprocess_marker(process, expected):
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(timeout=20), "Worker did not reach the controlled execution boundary"
        assert process.stdout.readline().strip() == expected
    finally:
        selector.close()


@pytest.mark.parametrize("during_provider", [False, True], ids=["after-claim", "during-provider"])
def test_process_killed_work_survives_and_production_cli_recovers_once(execution, during_provider):
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    script = """
import time
from app.config import Settings
from app.db import create_session_factory
from app.knowledge.storage import build_object_storage
from app.speech_transcription.execution import TranscriptionExecutionService as Service
from app.speech_transcription.runtime import build_transcription_provider
settings = Settings()
factory = create_session_factory(settings)
with factory() as session:
    job = Service.claim_next(session, worker_id='crash-proof')
    session.commit()
    identifier, token = job.id, job.claim_token
"""
    if during_provider:
        script += """
def blocking_factory(snapshot, settings):
    real = build_transcription_provider(snapshot, settings)
    class Blocking:
        provider, model_name, adapter_version = real.provider, real.model_name, real.adapter_version
        def transcribe(self, audio, **kwargs):
            print('PROVIDER_ENTERED', flush=True)
            time.sleep(60)
            return real.transcribe(audio, **kwargs)
    return Blocking()
Service.execute_claim(factory, job_id=identifier, claim_token=token,
                      storage=build_object_storage(settings),
                      provider_factory=blocking_factory, settings=settings)
"""
        marker = "PROVIDER_ENTERED"
    else:
        script += "print('CLAIM_COMMITTED', flush=True)\ntime.sleep(60)\n"
        marker = "CLAIM_COMMITTED"
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script],
        cwd=API_ROOT,
        env=execution.environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _subprocess_marker(process, marker)
        assert execution.job(queued.id).status == "RUNNING"
        assert execution.versions() == []
        process.kill()
        process.wait(timeout=10)
        execution.due(queued, expired_lease=True)
        execution.worker_once()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    assert execution.job(queued.id).status == "READY"
    assert execution.job(queued.id).attempt_count == 2
    assert len(execution.versions()) == 1


@pytest.mark.parametrize("scenario", ["timeout", "unavailable", "malformed", "empty"])
def test_provider_failures_never_publish_false_ready_or_modify_original_audio(execution, scenario):
    execution.profile(scenario=scenario)
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    claim = execution.claim()
    execution.execute(claim)
    failed = execution.job(queued.id)
    assert failed.status in {"RETRYABLE", "FAILED"}
    assert failed.failure_category
    assert execution.versions() == []
    asset = execution.asset(state["asset"])
    assert asset.status is CandidateMediaStatus.READY
    assert hashlib.sha256(execution.storage.get(asset.storage_key)).hexdigest() == asset.sha256


def test_retry_exhaustion_is_terminal_and_does_not_reset_attempts(execution):
    execution.profile(scenario="timeout")
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    for expected_attempt in range(1, queued.max_attempts + 1):
        claim = execution.claim()
        assert claim.attempt_count == expected_attempt
        execution.execute(claim)
        current = execution.job(queued.id)
        if expected_attempt < queued.max_attempts:
            assert current.status == "RETRYABLE"
            execution.due(current)
    assert execution.job(queued.id).status == "FAILED"
    assert execution.claim() is None
    assert execution.versions() == []
    response = execution.client.post(
        execution.org + f"/speech-transcription-jobs/{queued.id}/retry",
        headers=execution.login("admin@demo.prufai.local"),
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "invalid",
    [
        "provider",
        "model",
        "adapter",
        "language",
        "source-sha",
        "execution-id",
        "boolean-time",
        "negative-time",
        "duration-overflow",
        "overlap",
        "duplicate-segment",
        "undefined-confidence",
        "oversized-text",
        "control-text",
        "structure",
    ],
)
def test_strict_provider_output_is_rejected_before_version_publication(execution, invalid):
    state = execution.submitted()
    queued = execution.job(execution.request(state))

    def invalid_factory(snapshot, settings):
        real = build_transcription_provider(snapshot, settings)

        class InvalidOutput:
            provider, model_name, adapter_version = (
                real.provider,
                real.model_name,
                real.adapter_version,
            )

            def transcribe(self, audio, **kwargs):
                valid = real.transcribe(audio, **kwargs)
                if invalid in {"provider", "model", "adapter"}:
                    key = {
                        "provider": "provider",
                        "model": "model_name",
                        "adapter": "adapter_version",
                    }[invalid]
                    return replace(valid, **{key: "WRONG"})
                if invalid in {"language", "source-sha", "execution-id"}:
                    metadata = dict(valid.metadata)
                    metadata[
                        {
                            "language": "language",
                            "source-sha": "audio_sha256",
                            "execution-id": "execution_id",
                        }[invalid]
                    ] = "WRONG"
                    return replace(valid, metadata=metadata)
                segments = {
                    "boolean-time": (TranscriptionSegment("a", False, 1000, "TEST ONLY"),),
                    "negative-time": (TranscriptionSegment("a", -1, 1000, "TEST ONLY"),),
                    "duration-overflow": (TranscriptionSegment("a", 0, 1001, "TEST ONLY"),),
                    "overlap": (
                        TranscriptionSegment("a", 0, 600, "TEST ONLY"),
                        TranscriptionSegment("b", 500, 1000, "TEST ONLY"),
                    ),
                    "duplicate-segment": (
                        TranscriptionSegment("a", 0, 500, "TEST ONLY"),
                        TranscriptionSegment("a", 500, 1000, "TEST ONLY"),
                    ),
                    "undefined-confidence": (TranscriptionSegment("a", 0, 1000, "TEST ONLY", 0.5),),
                }
                if invalid in segments:
                    return replace(valid, segments=segments[invalid])
                if invalid == "oversized-text":
                    return replace(valid, transcript="TEST ONLY " + "x" * 200_001)
                if invalid == "control-text":
                    return replace(valid, transcript="TEST ONLY\x00")
                return {"transcript": "TEST ONLY invalid output structure"}

        return InvalidOutput()

    execution.execute(execution.claim(), provider_factory=invalid_factory)
    failed = execution.job(queued.id)
    assert failed.status == "FAILED" and failed.failure_category == "INVALID_PROVIDER_OUTPUT"
    assert execution.versions() == []
    assert execution.asset(state["asset"]).status is CandidateMediaStatus.READY


def test_transient_private_storage_failure_retries_same_pinned_job_without_fake_fallback(
    execution, monkeypatch
):
    state = execution.submitted()
    queued = execution.job(execution.request(state))

    def unavailable(_key, **_kwargs):
        raise StorageError("Private object store temporarily unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(execution.storage, "get_bounded", unavailable)
        execution.execute(execution.claim())
    failed = execution.job(queued.id)
    assert failed.status == "RETRYABLE" and execution.versions() == []
    retry_path = execution.org + f"/speech-transcription-jobs/{queued.id}/retry"
    assert execution.client.post(retry_path, headers=state["headers"]).status_code == 403
    retried = execution.call("POST", retry_path, execution.login("admin@demo.prufai.local"))
    assert retried["id"] == str(queued.id) and retried["attempt_count"] == 1
    execution.execute(execution.claim())
    assert execution.job(queued.id).status == "READY"
    assert execution.job(queued.id).attempt_count == 2
    assert len(execution.versions()) == 1


def test_completion_database_failure_after_provider_response_recovers_without_duplicate_version(
    execution,
):
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    claim = execution.claim()
    injected = []
    provider_returned = []

    def provider_factory(snapshot, settings):
        real = build_transcription_provider(snapshot, settings)

        class Observed:
            provider, model_name, adapter_version = (
                real.provider,
                real.model_name,
                real.adapter_version,
            )

            def transcribe(self, audio, **kwargs):
                result = real.transcribe(audio, **kwargs)
                provider_returned.append(True)
                return result

        return Observed()

    def fail_first_completion(_connection, _cursor, statement, _parameters, _context, _many):
        if not injected and statement.startswith("INSERT INTO speech_transcription_versions"):
            assert provider_returned == [True]
            injected.append(True)
            raise OperationalError("Technical test database completion failure", None, None)

    engine = execution.session.get_bind()
    event.listen(engine, "before_cursor_execute", fail_first_completion)
    try:
        execution.execute(claim, provider_factory=provider_factory)
    finally:
        event.remove(engine, "before_cursor_execute", fail_first_completion)
    assert injected == [True]
    assert execution.versions() == []
    current = execution.job(queued.id)
    assert current.status == "RETRYABLE"
    execution.due(current)
    execution.worker_once()
    assert execution.job(queued.id).status == "READY"
    assert len(execution.versions()) == 1


@pytest.mark.parametrize(
    "corruption",
    ["unsubmitted", "stale-revision", "audio-sha", "recording-sha", "incomplete-input", "duration"],
)
def test_invalid_or_stale_source_never_creates_execution_job(execution, corruption):
    state = execution.submitted(submit=corruption != "unsubmitted")
    body = {
        "operation_id": str(uuid.uuid4()),
        "profile_id": execution.ids["transcription_execution_profile_id"],
        "language": "de",
        "candidate_response_revision_id": str(state["revision"].id),
        "audio_sha256": state["asset"]["sha256"],
    }
    if corruption == "stale-revision":
        body["candidate_response_revision_id"] = str(uuid.uuid4())
    elif corruption == "audio-sha":
        body["audio_sha256"] = "0" * 64
    elif corruption in {"recording-sha", "incomplete-input", "duration"}:
        asset = execution.asset(state["asset"])
        with execution.factory() as session:
            if corruption == "recording-sha":
                session.execute(
                    update(CandidateAudioRecordingSession)
                    .where(CandidateAudioRecordingSession.id == asset.recording_session_id)
                    .values(upload_sha256="0" * 64)
                )
            elif corruption == "duration":
                session.execute(
                    update(CandidateMediaAsset)
                    .where(CandidateMediaAsset.id == asset.id)
                    .values(duration_ms=None)
                )
            else:
                provenance = copy.deepcopy(asset.validation_provenance)
                provenance["complete_input"] = False
                session.execute(
                    update(CandidateMediaAsset)
                    .where(CandidateMediaAsset.id == asset.id)
                    .values(validation_provenance=provenance)
                )
            session.commit()
    response = execution.client.post(
        execution.org + f"/candidate-media/{state['asset']['media_asset_id']}/transcriptions",
        headers=execution.login("admin@demo.prufai.local"),
        json=body,
    )
    assert response.status_code in {400, 409, 422}, response.text
    with execution.factory() as session:
        assert session.scalar(select(func.count()).select_from(SpeechTranscriptionJob)) == 0
    assert execution.versions() == []


def test_profile_snapshot_is_pinned_and_ready_history_cannot_be_rewritten(execution):
    state = execution.submitted()
    first = execution.job(execution.request(state))
    execution.profile(scenario="timeout")
    replay = execution.job(execution.request(state, operation=str(first.operation_id)))
    assert replay.id == first.id and replay.profile_version == first.profile_version
    with execution.factory() as session:
        current = session.get(AIModelProfile, first.profile_id)
        current.status = AIArtifactStatus.DRAFT
        session.commit()
    replay_disabled = execution.job(execution.request(state, operation=str(first.operation_id)))
    assert replay_disabled.id == first.id
    with execution.factory() as session:
        current = session.get(AIModelProfile, first.profile_id)
        current.status = AIArtifactStatus.ACTIVE
        session.commit()
    # The queued snapshot remains success, even after the current profile changes.
    execution.worker_once()
    assert execution.job(first.id).status == "READY"
    original = execution.versions()[0]
    original_payload = (
        original.transcript,
        copy.deepcopy(original.configuration),
        original.checksum,
    )
    execution.profile(scenario="success")
    successor = execution.job(execution.request(state))
    execution.worker_once()
    versions = execution.versions()
    assert len(versions) == 2 and versions[1].supersedes_version_id == original.id
    assert (
        versions[0].transcript,
        versions[0].configuration,
        versions[0].checksum,
    ) == original_payload
    assert versions[1].execution_job_id == successor.id
    with execution.factory() as session:
        historical = session.get(SpeechTranscriptionVersion, original.id)
        historical.configuration["nested-history-corruption"] = {"text": "changed"}
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()
    for model, identifier in (
        (SpeechTranscriptionVersion, original.id),
        (SpeechTranscriptionJob, first.id),
    ):
        with execution.factory() as session:
            session.delete(session.get(model, identifier))
            with pytest.raises(ValueError, match="immutable|cannot be deleted"):
                session.commit()
            session.rollback()


def test_revoked_requester_membership_prevents_worker_execution_and_content_access(execution):
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    with execution.factory() as session:
        administrator = session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
        membership = session.scalar(
            select(OrganisationMembership).where(
                OrganisationMembership.user_id == administrator.id,
                OrganisationMembership.organisation_id == queued.organisation_id,
            )
        )
        membership.status = MembershipStatus.SUSPENDED
        session.commit()
    execution.worker_once()
    assert execution.job(queued.id).status == "FAILED"
    assert execution.versions() == []


def test_candidate_foreign_tenant_and_platform_identity_cannot_request_transcript(execution):
    state = execution.submitted()
    path = execution.org + f"/candidate-media/{state['asset']['media_asset_id']}/transcriptions"
    body = {
        "operation_id": str(uuid.uuid4()),
        "profile_id": execution.ids["transcription_execution_profile_id"],
        "language": "de",
        "candidate_response_revision_id": str(state["revision"].id),
        "audio_sha256": state["asset"]["sha256"],
    }
    headers = [
        state["headers"],
        execution.login(execution.users[1].email),
        execution.login(
            "speaking.outsider@demo.prufai.local",
            org=str(execution.ids["speaking_human_outside_org_id"]),
        ),
        execution.login(PLATFORM_ADMIN_EMAIL, tenant=False),
    ]
    for actor in headers:
        response = execution.client.post(path, headers=actor, json=body)
        assert response.status_code in {400, 403, 404}, response.text
    with execution.factory() as session:
        assert session.scalar(select(func.count()).select_from(SpeechTranscriptionJob)) == 0


def test_request_rejects_arbitrary_private_object_and_credential_fields_before_queue(execution):
    state = execution.submitted()
    path = execution.org + f"/candidate-media/{state['asset']['media_asset_id']}/transcriptions"
    headers = execution.login("admin@demo.prufai.local")
    for field in ("storage_key", "api_key", "secret"):
        response = execution.client.post(
            path,
            headers=headers,
            json={
                "operation_id": str(uuid.uuid4()),
                "profile_id": execution.ids["transcription_execution_profile_id"],
                "language": "de",
                "candidate_response_revision_id": str(state["revision"].id),
                "audio_sha256": state["asset"]["sha256"],
                field: "TEST_ONLY_FORBIDDEN_REQUEST_VALUE",
            },
        )
        assert response.status_code == 422, (field, response.status_code)
    with execution.factory() as session:
        assert session.scalar(select(func.count()).select_from(SpeechTranscriptionJob)) == 0
    assert execution.versions() == []


def test_ready_transcript_only_in_authorized_tenant_view_and_never_operational_audit(execution):
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    execution.worker_once()
    version = execution.versions()[0]
    version_path = execution.org + (
        f"/speech-transcriptions/{queued.transcription_id}/versions/{version.id}"
    )
    allowed = execution.client.get(
        version_path, headers=execution.login("reviewer@demo.prufai.local")
    )
    assert allowed.status_code == 200 and allowed.json()["transcript"] == version.transcript
    denied = [
        state["headers"],
        execution.login(execution.users[1].email),
        execution.login(
            "speaking.outsider@demo.prufai.local",
            org=execution.ids["speaking_human_outside_org_id"],
        ),
        execution.login(PLATFORM_ADMIN_EMAIL, tenant=False),
        execution.login(PLATFORM_ADMIN_EMAIL),
    ]
    for actor in denied:
        response = execution.client.get(version_path, headers=actor)
        assert response.status_code in {400, 403, 404}, response.text
    platform = execution.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    operations = execution.call(
        "GET", "/api/v1/platform/control-center/ai-operations?kind=transcriptions", platform
    )
    activity = execution.call(
        "GET",
        "/api/v1/platform/control-center/audit?event_type=speech_transcription.completed",
        platform,
    )
    asset = execution.asset(state["asset"])
    serialized = json.dumps([operations, activity])
    assert str(queued.id) in serialized
    assert version.transcript not in serialized
    assert asset.storage_key not in serialized
    assert "configuration" not in json.dumps(operations)
    for name in ("OBJECT_STORAGE_SECRET_KEY", "OBJECT_STORAGE_ACCESS_KEY"):
        if os.getenv(name):
            assert os.environ[name] not in serialized
    with execution.factory() as session:
        audit = list(
            session.scalars(select(AuditEvent).where(AuditEvent.entity_id == str(queued.id)))
        )
        assert {row.event_type for row in audit} >= {
            "speech_transcription.requested",
            "speech_transcription.started",
            "speech_transcription.completed",
        }
        payload = json.dumps([row.metadata_json for row in audit])
        assert version.transcript not in payload and asset.storage_key not in payload
        safe = TranscriptionExecutionService.payload(
            session, session.get(SpeechTranscriptionJob, queued.id)
        )
        assert "transcript" not in safe and "storage_key" not in safe


@pytest.mark.parametrize("corruption", ["source-object", "response-revision"])
def test_worker_revalidates_source_after_queue_before_any_provider_call(execution, corruption):
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    if corruption == "source-object":
        asset = execution.asset(state["asset"])
        original = execution.storage.get(asset.storage_key)
        corrupted = bytes([original[0] ^ 1]) + original[1:]
        execution.storage.put(asset.storage_key, corrupted, asset.content_type)
    else:
        with execution.factory() as session:
            session.execute(
                text(
                    "UPDATE candidate_responses SET current_revision=current_revision+1 "
                    "WHERE attempt_id=:attempt"
                ),
                {"attempt": queued.attempt_id},
            )
            session.commit()
    called = []

    def forbidden_provider(_snapshot, _settings):
        called.append(True)
        raise AssertionError("Invalid source must not invoke provider")

    execution.execute(execution.claim(), provider_factory=forbidden_provider)
    assert called == []
    assert execution.job(queued.id).status == "FAILED"
    assert execution.versions() == []


def test_queued_execution_identity_nested_configuration_and_history_are_immutable(execution):
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    for change in ("profile", "source", "history", "sha"):
        with execution.factory() as session:
            job = session.get(SpeechTranscriptionJob, queued.id)
            if change == "profile":
                job.profile_snapshot["configuration"]["scenario"] = "timeout"
            elif change == "source":
                job.source_identity["recording_policy"]["max_recording_attempts"] = 999
            elif change == "history":
                job.state_history[0]["status"] = "READY"
            else:
                job.audio_sha256 = "0" * 64
            with pytest.raises(ValueError, match="immutable|append-only"):
                session.commit()
            session.rollback()
    assert execution.job(queued.id).status == "QUEUED"
    execution.worker_once()
    assert execution.job(queued.id).status == "READY"


def test_duplicate_concurrent_delivery_cannot_publish_two_authoritative_versions(execution):
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    claim = execution.claim()
    barrier = Barrier(2, timeout=15)
    entered = []
    guard = Lock()

    def overlapping_factory(snapshot, settings):
        real = build_transcription_provider(snapshot, settings)

        class Overlap:
            provider, model_name, adapter_version = (
                real.provider,
                real.model_name,
                real.adapter_version,
            )

            def transcribe(self, audio, **kwargs):
                result = real.transcribe(audio, **kwargs)
                with guard:
                    entered.append(True)
                barrier.wait()
                return result

        return Overlap()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(execution.execute, claim, provider_factory=overlapping_factory)
            for _ in range(2)
        ]
        results = [future.result(timeout=40) for future in futures]
    assert entered == [True, True]
    assert sum(result is not None for result in results) == 1
    assert execution.job(queued.id).status == "READY"
    assert len(execution.versions()) == 1


@pytest.mark.parametrize("claimed", [False, True], ids=["queued", "running"])
def test_authorized_cancellation_fences_delivery_and_preserves_original_evidence(
    execution, claimed
):
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    claim = execution.claim() if claimed else None
    path = execution.org + f"/speech-transcription-jobs/{queued.id}/cancel"
    assert execution.client.post(path, headers=state["headers"]).status_code == 403
    cancelled = execution.call("POST", path, execution.login("admin@demo.prufai.local"))
    assert cancelled["status"] == "CANCELLED" and cancelled["can_retry"] is False
    if claim:
        assert execution.execute(claim) is None
    assert execution.claim() is None and execution.versions() == []
    assert execution.asset(state["asset"]).status is CandidateMediaStatus.READY


def test_slow_execution_renews_real_lease_and_excludes_second_worker_after_original_expiry(
    execution,
):
    execution.profile(delay=3)
    state = execution.submitted()
    queued = execution.job(execution.request(state))
    claim = execution.claim(lease_seconds=1)
    original_expiry = claim.lease_expires_at.astimezone(timezone.utc)
    renewal = Event()
    engine = execution.session.get_bind()

    def renewed(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            statement.startswith("UPDATE speech_transcription_jobs")
            and "lease_expires_at" in statement
        ):
            renewal.set()

    event.listen(engine, "after_cursor_execute", renewed)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(execution.execute, claim)
            assert renewal.wait(timeout=5), "Actual PostgreSQL lease heartbeat never executed"
            deadline = time.monotonic() + 5
            while datetime.now(timezone.utc) <= original_expiry:
                assert time.monotonic() < deadline
                Event().wait(0.03)
            running = execution.job(queued.id)
            assert running.status == "RUNNING"
            assert running.lease_expires_at.astimezone(timezone.utc) > original_expiry
            assert execution.claim(worker="competing-worker") is None
            future.result(timeout=15)
    finally:
        event.remove(engine, "after_cursor_execute", renewed)
    assert execution.job(queued.id).status == "READY"
    assert len(execution.versions()) == 1


def test_platform_profile_independent_exact_version_approval_is_not_transfer_authority(execution):
    with execution.factory() as session:
        approver = IdentityService.create_user(
            session, email=f"transcription-platform-approver-{uuid.uuid4()}@example.test"
        )
        AuthenticationService(execution.settings).set_password(session, approver, PASSWORD)
        role = session.scalar(
            select(Role).where(Role.key == "PRUFAI_SUPER_ADMIN", Role.scope == RoleScope.PLATFORM)
        )
        session.add(PlatformRoleAssignment(user_id=approver.id, role_id=role.id))
        approver_email = approver.email
        session.commit()
    creator = execution.login(PLATFORM_ADMIN_EMAIL, tenant=False)
    second = execution.login(approver_email, tenant=False)
    config = {
        "schema_version": "1",
        "adapter_version": "1",
        "languages": ["de"],
        "endpoint": ENDPOINT,
        "region": "EU",
        "deployment_id": "technical-proof-project",
        "credential_reference": CREDENTIAL_REFERENCE,
        "model_configuration_version": "technical-proof-v1",
        "response_format": "json",
        "processing_approval_reference": "test:registry-governance-only",
        "processing": {key: "test:registry-governance-only" for key in PROCESSING_REFERENCES},
    }
    created = execution.call(
        "POST",
        "/api/v1/platform/ai/models",
        creator,
        expected=201,
        json={
            "code": "technical.transcription." + uuid.uuid4().hex,
            "name": "Technical registry approval test; no external transfer approved",
            "provider": "OPENAI",
            "model_name": "gpt-transcribe",
            "purpose": "SPEECH_TRANSCRIPTION",
            "configuration": config,
        },
    )
    assert created["status"] == "DRAFT"
    models = execution.call("GET", "/api/v1/platform/ai/models", creator)
    descriptor = next(row for row in models if row["id"] == created["id"])
    fingerprint = descriptor["transcription_profile_version"]
    assert len(fingerprint) == 64
    path = f"/api/v1/platform/ai/models/{created['id']}/approve-transcription"
    body = {"expected_profile_version": fingerprint}
    assert execution.client.post(path, headers=creator, json=body).status_code == 403
    assert (
        execution.client.post(
            path, headers=execution.login("admin@demo.prufai.local"), json=body
        ).status_code
        == 403
    )
    assert (
        execution.client.post(
            path, headers=second, json={"expected_profile_version": "0" * 64}
        ).status_code
        == 409
    )
    approved = execution.call("POST", path, second, json=body)
    assert approved["status"] == "ACTIVE"
    assert approved["external_processing_authorized"] is False
    with execution.factory() as session:
        profile = session.get(AIModelProfile, uuid.UUID(created["id"]))
        assert str(profile.approved_by) != execution.ids["platform_admin_user_id"]
        availability = validate_profile_configuration(
            snapshot_profile(profile, "de", {}), execution.settings
        )
        assert availability["availability"] == "UNCONFIGURED"
        assert availability["unavailable_reason"] == "DEPLOYMENT_APPROVAL_REQUIRED"
        assert session.scalar(select(func.count()).select_from(SpeechTranscriptionJob)) == 0
        assert session.scalar(select(func.count()).select_from(SpeechTranscriptionVersion)) == 0
    state = execution.submitted()
    rejected = execution.request(
        state,
        expected=409,
        body={
            "operation_id": str(uuid.uuid4()),
            "profile_id": created["id"],
            "language": "de",
            "candidate_response_revision_id": str(state["revision"].id),
            "audio_sha256": state["asset"]["sha256"],
        },
    )
    assert rejected["detail"] == "DEPLOYMENT_APPROVAL_REQUIRED"
    with execution.factory() as session:
        assert session.scalar(select(func.count()).select_from(SpeechTranscriptionJob)) == 0
