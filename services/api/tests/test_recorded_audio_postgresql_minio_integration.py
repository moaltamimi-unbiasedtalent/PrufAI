"""Normal JWT recorded evidence against Alembic PostgreSQL and private real S3.

Original generated PCM tones prove media transport/decoding only, never speech,
transcription or assessment. No memory storage/client or successful API is faked.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event, Lock
from typing import Any, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.api import auth_settings_dependency
from app.auth.service import AuthenticationService
from app.authorization import build_tenant_context
from app.authorization.seed import seed_defaults
from app.config import Settings, get_settings
from app.db import get_db_session
from app.knowledge.storage import S3CompatibleObjectStorage, build_object_storage
from app.main import app
from app.models import (
    AIRun,
    AuditEvent,
    CandidateAudioRecordingSession,
    CandidateExamAttempt,
    CandidateMediaAsset,
    CandidateResponse,
    CandidateResponseMediaBinding,
    CandidateResponseRevision,
    MembershipRole,
    Organisation,
    OrganisationMembership,
    Role,
    User,
)
from app.models.enums import CandidateMediaStatus, OrganisationType, RoleScope
from app.runtime.service import CandidateRuntimeService, _payload_checksum
from app.scripts.seed_audio_demo import AUDIO_POLICY, reconcile_audio_demo
from app.scripts.seed_demo import (
    PASSWORD,
    PLATFORM_ADMIN_EMAIL,
    _platform_user,
    _users,
    reconcile_demo,
)
from app.services.identity import IdentityService
from app.services.organisations import OrganisationService
from app.speaking_audio.media import MAX_INSPECTION_OUTPUT, validate_recorded_media
from tests.alembic_helpers import repository_alembic_head
from tests.integration_support.audio_evidence import technical_encoded_audio, technical_tone_wav
from tests.test_audio_demo_seed import assert_audio_seed_preconditions

REQUIRED = (
    os.getenv("PRUFAI_REQUIRE_POSTGRES_INTEGRATION") == "1"
    or os.getenv("PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION") == "1"
)
pytestmark = pytest.mark.skipif(
    not (os.getenv("POSTGRES_INTEGRATION_URL") and os.getenv("OBJECT_STORAGE_ENDPOINT"))
    and not REQUIRED,
    reason="recorded evidence requires real PostgreSQL AND private MinIO/S3",
)


def _settings() -> Settings:
    names = (
        "POSTGRES_INTEGRATION_URL",
        "OBJECT_STORAGE_ENDPOINT",
        "OBJECT_STORAGE_BUCKET",
        "OBJECT_STORAGE_ACCESS_KEY",
        "OBJECT_STORAGE_SECRET_KEY",
    )
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        pytest.fail(
            "Required recorded-audio gate missing configuration names: " + ", ".join(missing)
        )
    return Settings(
        _env_file=None,
        environment="test",
        object_storage_backend="s3",
        auth_rate_limit_backend="memory",
        auth_rate_limit_login_attempts=500,
        auth_jwt_secret="recorded-evidence-real-jwt-" + "x" * 40,
    )


def _stable_recording(projection):
    """Clock observations advance on replay; evidence identity must not."""
    return {
        key: value
        for key, value in projection.items()
        if key not in {"server_now", "recording_remaining_ms", "upload_remaining_ms"}
    }


def _assert_no_response_attachment(audio, state):
    audio.session.expire_all()
    attempt = uuid.UUID(state["id"])
    assert (
        audio.session.scalar(
            select(func.count())
            .select_from(CandidateResponse)
            .where(CandidateResponse.attempt_id == attempt)
        )
        == 0
    )
    assert (
        audio.session.scalar(
            select(func.count())
            .select_from(CandidateResponseRevision)
            .where(CandidateResponseRevision.attempt_id == attempt)
        )
        == 0
    )
    assert (
        audio.session.scalar(
            select(func.count())
            .select_from(CandidateResponseMediaBinding)
            .join(
                CandidateResponseRevision,
                CandidateResponseRevision.id
                == CandidateResponseMediaBinding.candidate_response_revision_id,
            )
            .where(CandidateResponseRevision.attempt_id == attempt)
        )
        == 0
    )


@dataclass
class RecordedAudio:
    session: Session
    factory: Any
    client: TestClient
    settings: Settings
    ids: dict[str, str]
    users: list[User]
    foreign_org: uuid.UUID
    storage: S3CompatibleObjectStorage

    @property
    def org(self) -> str:
        return "/api/v1/organisations/" + self.ids["organisation_id"]

    def login(self, email: str, *, org: str | None = None, tenant: bool = True) -> dict[str, str]:
        response = self.client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        if tenant:
            headers["X-PrufAI-Organisation-ID"] = org or self.ids["organisation_id"]
        return headers

    def call(self, method: str, path: str, headers: dict[str, str], *, expected=200, **kwargs):
        response = self.client.request(method, path, headers=headers, **kwargs)
        assert response.status_code == expected, (method, path, response.status_code, response.text)
        return response.json() if response.content else None

    def start(self, index: int = 0) -> dict[str, Any]:
        admin = self.login("admin@demo.prufai.local")
        assignment = self.call(
            "POST",
            self.org + "/exam-assignments",
            admin,
            expected=201,
            json={
                "candidate_user_id": str(self.users[index].id),
                "exam_version_id": self.ids["audio_exam_version_id"],
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
        assert len(items) == 2
        assert all(item["content"]["audio_policy"] == AUDIO_POLICY for item in items)
        return {"id": attempt["id"], "path": path, "headers": headers, "items": items}

    def record(self, state, item=0, *, operation=None, expected=200, headers=None):
        return self.call(
            "POST",
            f"/api/v1/candidate/attempts/{state['id']}/items/"
            f"{state['items'][item]['binding_id']}/audio-recordings",
            headers or state["headers"],
            expected=expected,
            json={"operation_id": operation or str(uuid.uuid4())},
        )

    def finalize(self, state, recording, *, content=None, expected=200, mime="audio/wav"):
        return self.call(
            "POST",
            "/api/v1/candidate/audio-recordings/" + recording["recording_session_id"] + "/finalize",
            {**state["headers"], "Content-Type": mime, "X-Filename": "original-technical-tone.wav"},
            expected=expected,
            content=technical_tone_wav() if content is None else content,
        )

    def attach(
        self,
        state,
        asset,
        item=0,
        *,
        expected_revision=0,
        operation=None,
        expected=200,
        headers=None,
    ):
        return self.call(
            "PUT",
            state["path"] + "/responses/" + state["items"][item]["binding_id"],
            headers or state["headers"],
            expected=expected,
            json={
                "client_operation_id": operation or str(uuid.uuid4()),
                "expected_revision": expected_revision,
                "payload": {"media_asset_id": asset["media_asset_id"]},
            },
        )

    def evidence(self, state, item=0):
        return self.call(
            "GET",
            f"/api/v1/candidate/attempts/{state['id']}/items/"
            f"{state['items'][item]['binding_id']}/audio-evidence",
            state["headers"],
        )

    def asset(self, asset):
        self.session.expire_all()
        return self.session.get(CandidateMediaAsset, uuid.UUID(asset["media_asset_id"]))


@pytest.fixture
def audio() -> Iterator[RecordedAudio]:
    settings = _settings()
    engine = create_engine(os.environ["POSTGRES_INTEGRATION_URL"])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    storage = build_object_storage(settings)
    assert isinstance(storage, S3CompatibleObjectStorage)
    storage.client.head_bucket(Bucket=storage.bucket)
    previous = dict(app.dependency_overrides)

    def database():
        with factory() as session:
            yield session

    try:
        with factory() as session:
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version"))
                == repository_alembic_head()
            )
            ids = reconcile_demo(
                session, Settings(_env_file=None, environment="development"), enabled=True
            )
            admin = session.scalar(select(User).where(User.email == "admin@demo.prufai.local"))
            context = build_tenant_context(session, admin.id, uuid.UUID(ids["organisation_id"]))
            users = []
            for label in ("candidate", "other", "foreign"):
                user = IdentityService.create_user(
                    session, email=f"audio-{label}-{uuid.uuid4()}@example.test"
                )
                AuthenticationService(settings).set_password(session, user, PASSWORD)
                users.append(user)
            for user in users[:2]:
                membership = OrganisationService.create_membership(
                    session, context, organisation_id=context.organisation_id, user_id=user.id
                )
                OrganisationService.assign_membership_role(
                    session, context, membership_id=membership.id, role_key="EXAMINER"
                )
            foreign = Organisation(
                slug="audio-foreign-" + uuid.uuid4().hex,
                name="Other real-PG audio tenant",
                organisation_type=OrganisationType.LANGUAGE_SCHOOL,
            )
            session.add(foreign)
            session.flush()
            membership = OrganisationMembership(organisation_id=foreign.id, user_id=users[2].id)
            session.add(membership)
            session.flush()
            role = session.scalar(
                select(Role).where(Role.key == "INSTITUTION_ADMIN", Role.scope == RoleScope.TENANT)
            )
            session.add(MembershipRole(membership_id=membership.id, role_id=role.id))
            session.commit()
            app.dependency_overrides[get_db_session] = database
            app.dependency_overrides[auth_settings_dependency] = lambda: settings
            app.dependency_overrides[get_settings] = lambda: settings
            with TestClient(app) as client:
                yield RecordedAudio(
                    session, factory, client, settings, ids, users, foreign.id, storage
                )
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


def test_audio_seed_real_postgresql_is_governed_unassigned_and_repeatable(audio):
    first = assert_audio_seed_preconditions(audio.session)
    assert assert_audio_seed_preconditions(audio.session) == first


def test_record_finalize_attach_reload_submit_and_reviewer_evidence(audio):
    before_ai = audio.session.scalar(select(func.count()).select_from(AIRun))
    state = audio.start()
    assets = []
    for item in (0, 1):
        recording = audio.record(state, item)
        asset = audio.finalize(state, recording)
        assert asset["status"] == "READY" and asset["duration_ms"] == 1000
        assert asset["sha256"] == hashlib.sha256(technical_tone_wav()).hexdigest()
        assert asset["validation_provenance"] and asset["byte_size"] == len(technical_tone_wav())
        row = audio.asset(asset)
        assert audio.storage.size(row.storage_key) == row.byte_size
        assert audio.storage.get(row.storage_key) == technical_tone_wav()
        saved = audio.attach(state, asset, item)
        assert saved["revision"] == 1
        evidence = audio.evidence(state, item)
        assert len(evidence["recordings"]) == 1
        assert evidence["recordings"][0]["asset"]["attachment_status"] == "ATTACHED"
        assets.append(asset)
    assert assets[0]["media_asset_id"] != assets[1]["media_asset_id"]
    reloaded = audio.call("GET", state["path"] + "/runtime", state["headers"])
    assert len(reloaded["responses"]) == 2
    audio.call("POST", state["path"] + "/submit", state["headers"])
    for item, asset in enumerate(assets):
        audio.attach(state, asset, item, expected_revision=1, expected=409)
        audio.record(state, item, expected=409)
    reviewer = audio.login("reviewer@demo.prufai.local")
    reviewed = audio.call("GET", audio.org + "/audio-evidence", reviewer)
    assert state["id"] in json.dumps(reviewed)
    assert audio.session.scalar(select(func.count()).select_from(AIRun)) == before_ai


def test_wrong_item_candidate_tenant_and_stale_revision_never_bind(audio):
    state, other = audio.start(), audio.start(1)
    asset = audio.finalize(state, audio.record(state))
    audio.attach(state, asset, 1, expected=409)
    audio.attach(other, asset, expected=403)
    own_other_attempt = audio.start()
    audio.attach(own_other_attempt, asset, expected=409)
    stranger = audio.login(audio.users[1].email)
    audio.attach(state, asset, headers=stranger, expected=403)
    foreign = audio.login(audio.users[2].email, org=str(audio.foreign_org))
    audio.attach(state, asset, headers=foreign, expected=403)
    audio.attach(state, asset, expected_revision=1, expected=409)
    audio.session.expire_all()
    assert (
        audio.session.scalar(
            select(func.count())
            .select_from(CandidateResponseMediaBinding)
            .where(
                CandidateResponseMediaBinding.candidate_media_asset_id
                == uuid.UUID(asset["media_asset_id"])
            )
        )
        == 0
    )
    audio.attach(state, asset)
    audio.attach(state, asset, expected_revision=0, expected=409)
    row = audio.asset(asset)
    with pytest.raises(ValueError), audio.session.begin_nested():
        row.sha256 = "f" * 64
        audio.session.flush()
    audio.session.expire_all()
    assert audio.asset(asset).sha256 == hashlib.sha256(technical_tone_wav()).hexdigest()


def test_mismatched_item_version_checksum_cannot_attach_or_increment_revision(audio):
    state = audio.start()
    asset = audio.finalize(state, audio.record(state))
    attempt_id = uuid.UUID(state["id"])
    attempt = audio.session.get(CandidateExamAttempt, attempt_id)
    original_plan = copy.deepcopy(attempt.runtime_plan)
    inconsistent = copy.deepcopy(original_plan)
    for module in inconsistent["modules"]:
        for section in module["sections"]:
            for task in section["tasks"]:
                for item in task["items"]:
                    if item["binding_id"] == state["items"][0]["binding_id"]:
                        item["item_version_checksum"] = "f" * 64
    # Deliberate fixture-only storage inconsistency in this disposable database:
    # simulate stale item-version provenance without weakening production guards.
    table = CandidateExamAttempt.__table__
    try:
        audio.session.execute(
            table.update().where(table.c.id == attempt_id).values(runtime_plan=inconsistent)
        )
        audio.session.commit()
        rejected = audio.attach(state, asset, expected=409)
        assert "item version provenance" in rejected["detail"]
        _assert_no_response_attachment(audio, state)
    finally:
        audio.session.rollback()
        audio.session.execute(
            table.update().where(table.c.id == attempt_id).values(runtime_plan=original_plan)
        )
        audio.session.commit()
        audio.session.expire_all()
    assert audio.attach(state, asset)["revision"] == 1


def test_unready_owned_asset_cannot_attach_or_invent_ready_history(audio):
    state = audio.start()
    asset = audio.finalize(state, audio.record(state))
    asset_id = uuid.UUID(asset["media_asset_id"])
    table = CandidateMediaAsset.__table__
    # Fixture-only controlled non-READY row represents an inconsistent/interrupted
    # legacy pipeline. Real PUT/verified bytes and every ownership key stay exact.
    try:
        audio.session.execute(
            table.update()
            .where(table.c.id == asset_id)
            .values(status=CandidateMediaStatus.VALIDATING)
        )
        audio.session.commit()
        audio.attach(state, asset, expected=409)
        _assert_no_response_attachment(audio, state)
        assert audio.asset(asset).status is CandidateMediaStatus.VALIDATING
    finally:
        audio.session.rollback()
        audio.session.execute(
            table.update().where(table.c.id == asset_id).values(status=CandidateMediaStatus.READY)
        )
        audio.session.commit()
        audio.session.expire_all()
    assert audio.attach(state, asset)["revision"] == 1


def test_server_timing_allowance_advances_on_replay_metadata_and_terminal_state(audio):
    state = audio.start()
    operation = str(uuid.uuid4())
    first = audio.record(state, operation=operation)
    replay = audio.record(state, operation=operation)
    metadata = audio.evidence(state)["recordings"][0]
    for projection in (first, replay, metadata):
        assert datetime.fromisoformat(projection["server_now"]).tzinfo is not None
        assert 0 < projection["recording_remaining_ms"] <= 30_000
        assert 0 < projection["upload_remaining_ms"] <= 150_000
    assert replay["recording_remaining_ms"] <= first["recording_remaining_ms"]
    assert metadata["recording_remaining_ms"] <= replay["recording_remaining_ms"]
    asset = audio.finalize(state, first)
    finished = audio.evidence(state)["recordings"][0]
    assert finished["status"] == "READY"
    assert finished["recording_remaining_ms"] == finished["upload_remaining_ms"] == 0
    assert finished["asset"]["media_asset_id"] == asset["media_asset_id"]


def test_start_operation_replay_cancel_and_attempt_limit_are_persisted(audio):
    state = audio.start()
    operation = str(uuid.uuid4())
    first = audio.record(state, operation=operation)
    replay = audio.record(state, operation=operation)
    assert _stable_recording(replay) == _stable_recording(first)
    audio.record(state, 1, operation=operation, expected=409)
    audio.record(
        state, operation=operation, headers=audio.login(audio.users[1].email), expected=403
    )
    audio.record(state, expected=409)
    cancelled = audio.call(
        "POST",
        "/api/v1/candidate/audio-recordings/" + first["recording_session_id"] + "/cancel",
        state["headers"],
    )
    assert cancelled["status"] == "CANCELLED"
    second = audio.record(state)
    assert second["attempt_number"] == 2
    audio.call(
        "POST",
        "/api/v1/candidate/audio-recordings/" + second["recording_session_id"] + "/cancel",
        state["headers"],
    )
    audio.record(state, expected=409)
    assert len(audio.evidence(state)["recordings"]) == 2


def test_identical_audio_is_independently_owned_across_candidates(audio):
    states = [audio.start(index) for index in (0, 1)]
    assets = [audio.finalize(state, audio.record(state)) for state in states]
    rows = [audio.asset(asset) for asset in assets]
    assert rows[0].sha256 == rows[1].sha256
    assert rows[0].id != rows[1].id and rows[0].storage_key != rows[1].storage_key
    for state, asset, row in zip(states, assets, rows):
        audio.attach(state, asset)
        assert audio.storage.get(row.storage_key) == technical_tone_wav()


def test_http_range_reads_real_private_backend_and_authorization(audio, monkeypatch):
    state = audio.start()
    asset = audio.finalize(state, audio.record(state))
    audio.attach(state, asset)
    second = audio.finalize(state, audio.record(state, 1))
    audio.attach(state, second, 1)
    audio.call("POST", state["path"] + "/submit", state["headers"])
    row = audio.asset(asset)
    path = "/api/v1/candidate/audio-assets/" + asset["media_asset_id"] + "/stream"
    calls = []
    original = S3CompatibleObjectStorage.read_range

    def real_range(storage, key, start, end):
        calls.append((key, start, end))
        return original(storage, key, start, end)

    monkeypatch.setattr(S3CompatibleObjectStorage, "read_range", real_range)
    payload = technical_tone_wav()
    full = audio.client.get(path, headers=state["headers"])
    assert full.status_code == 200 and full.content == payload
    original_get = S3CompatibleObjectStorage.get

    def forbidden_full_read(*args, **kwargs):
        pytest.fail("206 must use real bounded backend Range, never full GET followed by slicing")

    monkeypatch.setattr(S3CompatibleObjectStorage, "get", forbidden_full_read)
    for header, bounds in (
        ("bytes=100-199", (100, 199)),
        ("bytes=100-", (100, len(payload) - 1)),
        ("bytes=-73", (len(payload) - 73, len(payload) - 1)),
    ):
        response = audio.client.get(path, headers={**state["headers"], "Range": header})
        assert response.status_code == 206
        assert response.content == payload[bounds[0] : bounds[1] + 1]
        assert calls[-1] == (row.storage_key, *bounds)
    monkeypatch.setattr(S3CompatibleObjectStorage, "get", original_get)
    for malformed in (
        "",
        "invalid",
        "bytes=0-5,7-9",
        "bytes=999999999-",
        "bytes=-0",
        "bytes=+10-20",
        "bytes=1_0-20",
        "bytes=0-1_0",
        "bytes=-1_0",
        "bytes= 10-20",
        "bytes=0-+10",
        "bytes=0--1",
        "bytes=--10",
        "bytes=1-2-3",
        "bytes=١-٢".encode(),
        "bytes=１-２".encode(),
    ):
        prior_range_reads = len(calls)
        assert (
            audio.client.get(path, headers={**state["headers"], "Range": malformed}).status_code
            == 416
        )
        assert len(calls) == prior_range_reads
    assert audio.client.get(path).status_code == 401
    assert audio.client.get(path, headers=audio.login(audio.users[1].email)).status_code == 404
    assert (
        audio.client.get(
            path, headers=audio.login(audio.users[2].email, org=str(audio.foreign_org))
        ).status_code
        == 404
    )
    reviewer_path = (
        audio.org
        + f"/candidate-attempts/{state['id']}/items/{state['items'][0]['binding_id']}"
        + f"/audio-assets/{asset['media_asset_id']}/stream"
    )
    assert (
        audio.client.get(
            reviewer_path, headers=audio.login("reviewer@demo.prufai.local")
        ).status_code
        == 200
    )
    assert (
        audio.client.get(
            reviewer_path, headers=audio.login(PLATFORM_ADMIN_EMAIL, tenant=False)
        ).status_code
        == 403
    )
    # Unsigned direct S3 reads must not expose the same uploaded object.
    import httpx

    anonymous = httpx.get(
        audio.settings.object_storage_endpoint.rstrip("/")
        + "/"
        + audio.storage.bucket
        + "/"
        + row.storage_key
    )
    assert anonymous.status_code in (401, 403)


def test_two_real_connections_same_operation_start_one_recording(audio):
    state = audio.start()
    headers = [audio.login(audio.users[0].email) for _ in range(2)]
    operation = str(uuid.uuid4())
    barrier, guard = Barrier(2), Lock()
    participants = set()

    def overlap(connection, cursor, statement, parameters, context, executemany):
        if "candidate_exam_attempts" not in statement or "FOR UPDATE" not in statement:
            return
        pid = connection.connection.driver_connection.info.backend_pid
        with guard:
            first = pid not in participants
            participants.add(pid)
        if first:
            barrier.wait(timeout=8)

    event.listen(audio.factory.kw["bind"], "before_cursor_execute", overlap)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(audio.record, state, operation=operation, headers=header)
                for header in headers
            ]
            responses = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(audio.factory.kw["bind"], "before_cursor_execute", overlap)
    assert len(participants) == 2
    assert _stable_recording(responses[0]) == _stable_recording(responses[1])
    audio.session.expire_all()
    assert (
        audio.session.scalar(
            select(func.count())
            .select_from(CandidateAudioRecordingSession)
            .where(CandidateAudioRecordingSession.attempt_id == uuid.UUID(state["id"]))
        )
        == 1
    )


def test_pending_upload_or_missing_attachment_blocks_submission_and_replay_is_stable(audio):
    state = audio.start()
    first_operation = str(uuid.uuid4())
    recording = audio.record(state, operation=first_operation)
    audio.call("POST", state["path"] + "/submit", state["headers"], expected=409)
    first = audio.finalize(state, recording)
    audio.call("POST", state["path"] + "/submit", state["headers"], expected=409)
    operation = str(uuid.uuid4())
    saved = audio.attach(state, first, operation=operation)
    second = audio.finalize(state, audio.record(state, 1))
    audio.attach(state, second, 1)
    audio.call("POST", state["path"] + "/submit", state["headers"])
    # A committed request is recoverable after submission; no new operation mutates it.
    assert (
        audio.record(state, operation=first_operation)["recording_session_id"]
        == recording["recording_session_id"]
    )
    assert audio.finalize(state, recording)["media_asset_id"] == first["media_asset_id"]
    assert audio.attach(state, first, operation=operation) == saved
    audio.attach(state, first, expected_revision=1, expected=409)
    audio.session.expire_all()
    revisions = list(
        audio.session.scalars(
            select(CandidateResponseRevision).where(
                CandidateResponseRevision.attempt_id == uuid.UUID(state["id"])
            )
        )
    )
    assert len(revisions) == 2


def test_real_media_rejects_undecodable_mime_and_duration_and_expired_upload(audio):
    state = audio.start()
    first = audio.record(state)
    audio.finalize(
        state, first, content=b"x" * (AUDIO_POLICY["max_upload_bytes"] + 1), expected=400
    )
    audio.finalize(state, first, content=b"RIFF" + b"0" * 30, expected=400)
    second = audio.record(state)
    audio.finalize(state, second, content=technical_tone_wav(duration_seconds=31), expected=400)
    other = audio.record(state, 1)
    audio.session.expire_all()
    recording = audio.session.get(
        CandidateAudioRecordingSession, uuid.UUID(other["recording_session_id"])
    )
    recording.upload_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    audio.session.commit()
    audio.finalize(state, other, expected=409)
    audio.session.expire_all()
    assert (
        audio.session.get(CandidateAudioRecordingSession, recording.id).status
        is CandidateMediaStatus.EXPIRED
    )
    assert (
        audio.session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.event_type == "candidate_audio.recording_expired",
                AuditEvent.entity_id == str(recording.id),
            )
        )
        == 1
    )
    assert (
        audio.session.scalar(
            select(func.count())
            .select_from(CandidateMediaAsset)
            .where(CandidateMediaAsset.attempt_id == uuid.UUID(state["id"]))
        )
        == 0
    )


@pytest.mark.parametrize(
    "case",
    [
        "contiguous_over_limit",
        "decoded_gap_over_limit",
        "presentation_gap_over_limit",
        "wrong_codec",
        "unsupported_container",
        "truncated",
        "malformed",
    ],
)
def test_complete_media_rejection_never_readies_or_attaches_and_valid_retry_survives(audio, case):
    state = audio.start()
    recording = audio.record(state)
    mime = "audio/webm;codecs=opus"
    if case == "contiguous_over_limit":
        content = technical_encoded_audio(audio.settings, duration_seconds=31)
    elif case == "decoded_gap_over_limit":
        content = technical_encoded_audio(
            audio.settings, duration_seconds=40, timestamp_gap_seconds=60
        )
    elif case == "presentation_gap_over_limit":
        content = technical_encoded_audio(
            audio.settings, duration_seconds=2, timestamp_gap_seconds=60
        )
    elif case == "wrong_codec":
        content = technical_encoded_audio(audio.settings, codec_override="libvorbis")
    elif case == "unsupported_container":
        content = technical_encoded_audio(audio.settings, mime_type="audio/mpeg")
    elif case == "truncated":
        complete = technical_tone_wav()
        content, mime = complete[: len(complete) // 2], "audio/wav"
    else:
        content, mime = b"RIFF" + b"0" * 30, "audio/wav"
    audio.finalize(state, recording, content=content, mime=mime, expected=400)
    audio.session.expire_all()
    stage = audio.session.get(
        CandidateAudioRecordingSession, uuid.UUID(recording["recording_session_id"])
    )
    assert stage.status is CandidateMediaStatus.REJECTED
    assert stage.upload_sha256 is None
    assert not audio.storage.exists(stage.storage_key)
    assert (
        audio.session.scalar(
            select(func.count())
            .select_from(CandidateMediaAsset)
            .where(CandidateMediaAsset.recording_session_id == stage.id)
        )
        == 0
    )
    _assert_no_response_attachment(audio, state)
    # Rejection consumes the accepted capture, but does not poison the next
    # governed recording operation or fabricate evidence for the rejected input.
    replacement = audio.record(state)
    assert replacement["attempt_number"] == 2
    accepted = audio.finalize(state, replacement)
    assert accepted["duration_ms"] == 1000
    assert audio.attach(state, accepted)["revision"] == 1


def test_media_lifecycle_and_access_audit_never_contains_private_content(audio):
    state = audio.start()
    asset = audio.finalize(state, audio.record(state))
    audio.attach(state, asset)
    path = "/api/v1/candidate/audio-assets/" + asset["media_asset_id"] + "/stream"
    assert audio.client.get(path, headers=state["headers"]).status_code == 200
    assert audio.client.get(path, headers=state["headers"]).status_code == 200
    audio.session.expire_all()
    row = audio.asset(asset)
    events = list(
        audio.session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type.like("candidate_audio.%"),
                AuditEvent.actor_id == audio.users[0].id,
            )
        )
    )
    assert {entry.event_type for entry in events} >= {
        "candidate_audio.recording_started",
        "candidate_audio.finalized",
        "candidate_audio.accessed",
    }
    assert sum(entry.event_type == "candidate_audio.accessed" for entry in events) == 1
    for entry in events:
        serialized = json.dumps(entry.metadata_json)
        assert row.storage_key not in serialized
        assert audio.settings.object_storage_endpoint not in serialized
        assert "RIFF" not in serialized and "audio_bytes" not in serialized
        assert "speech" not in serialized.lower() and "transcript" not in serialized.lower()


def test_object_uploaded_but_database_receipt_failure_recovers_same_private_identity(
    audio, monkeypatch
):
    state = audio.start()
    recording = audio.record(state)
    original_flush = Session.flush
    failed = []

    def receipt_failure(session, objects=None):
        if not failed and any(isinstance(row, CandidateMediaAsset) for row in session.new):
            failed.append(True)
            raise SQLAlchemyError("Injected local database receipt outage after actual private PUT")
        return original_flush(session, objects)

    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", receipt_failure)
        audio.finalize(state, recording, expected=503)
    assert failed == [True]
    audio.session.expire_all()
    stage = audio.session.get(
        CandidateAudioRecordingSession, uuid.UUID(recording["recording_session_id"])
    )
    assert stage.status is CandidateMediaStatus.UPLOADING
    # The official interrupted-finalization state has a durable recording
    # receipt/object but no authoritative asset yet. Its deterministic future
    # identity cannot be attached before recovery makes the asset READY.
    future_asset_id = uuid.uuid5(stage.id, "primary-audio")
    assert audio.session.get(CandidateMediaAsset, future_asset_id) is None
    audio.attach(state, {"media_asset_id": str(future_asset_id)}, expected=400)
    _assert_no_response_attachment(audio, state)
    assert audio.session.get(CandidateMediaAsset, future_asset_id) is None
    staged_metadata = audio.evidence(state)["recordings"][0]
    assert staged_metadata["recording_remaining_ms"] == 0
    assert 0 < staged_metadata["upload_remaining_ms"] <= 150_000
    key = stage.storage_key
    assert stage.upload_sha256 == hashlib.sha256(technical_tone_wav()).hexdigest()
    assert audio.storage.get(key) == technical_tone_wav()
    # A newly constructed backend reaches the durable same object after the outage.
    restarted_backend = build_object_storage(audio.settings)
    assert restarted_backend.get(key) == technical_tone_wav()
    recovered = audio.call(
        "POST",
        "/api/v1/candidate/audio-recordings/" + recording["recording_session_id"] + "/recover",
        state["headers"],
    )["asset"]
    assert recovered["media_asset_id"] == str(future_asset_id)
    assert audio.asset(recovered).storage_key == key
    assert audio.finalize(state, recording)["media_asset_id"] == recovered["media_asset_id"]
    assert (
        audio.call(
            "POST",
            "/api/v1/candidate/audio-recordings/" + recording["recording_session_id"] + "/recover",
            state["headers"],
        )["asset"]["media_asset_id"]
        == recovered["media_asset_id"]
    )
    assert len(audio.evidence(state)["recordings"]) == 1
    audio.attach(state, recovered)


def test_legacy_prefix_validated_stage_cannot_become_new_ready_evidence(audio):
    state = audio.start()
    recording = audio.record(state)
    recording_id = uuid.UUID(recording["recording_session_id"])
    stage = audio.session.get(CandidateAudioRecordingSession, recording_id)
    content = technical_encoded_audio(audio.settings, duration_seconds=40, timestamp_gap_seconds=60)
    digest = hashlib.sha256(content).hexdigest()
    old_proof = {
        "validator": "ffprobe_and_complete_ffmpeg_audio_decode",
        "duration_source": "decoded_pcm_frame_samples",
        "duration_ms": 1054,
        "source_sha256": digest,
        "source_byte_size": len(content),
    }
    # Reconstruct an actual pre-correction interrupted receipt in this isolated
    # database: the old prefix decoder accepted 40s + 60s PTS gap as 1054ms.
    # The object PUT is real; this fixture-only SQL represents old persisted
    # history without weakening the application's immutable-history guards.
    audio.storage.put(stage.storage_key, content, "audio/webm;codecs=opus")
    table = CandidateAudioRecordingSession.__table__
    with audio.factory.kw["bind"].begin() as connection:
        connection.execute(
            table.update()
            .where(table.c.id == recording_id)
            .values(
                status=CandidateMediaStatus.UPLOADING,
                upload_sha256=digest,
                upload_byte_size=len(content),
                upload_content_type="audio/webm;codecs=opus",
                verified_duration_ms=1054,
                validation_provenance=old_proof,
                upload_started_at=datetime.now(timezone.utc),
            )
        )
    audio.session.expire_all()
    rejected = audio.call(
        "POST",
        "/api/v1/candidate/audio-recordings/" + str(recording_id) + "/recover",
        state["headers"],
        expected=409,
    )
    assert "predates complete-input validation" in rejected["detail"]
    retried = audio.finalize(
        state, recording, content=content, mime="audio/webm;codecs=opus", expected=409
    )
    assert "predates complete-input validation" in retried["detail"]
    _assert_no_response_attachment(audio, state)
    assert (
        audio.session.scalar(
            select(func.count())
            .select_from(CandidateMediaAsset)
            .where(CandidateMediaAsset.recording_session_id == recording_id)
        )
        == 0
    )
    old_stage = audio.session.get(CandidateAudioRecordingSession, recording_id)
    assert old_stage.status is CandidateMediaStatus.UPLOADING
    assert old_stage.verified_duration_ms == 1054
    assert old_stage.validation_provenance == old_proof
    assert old_stage.upload_sha256 == digest
    assert audio.storage.get(old_stage.storage_key) == content
    cancelled = audio.call(
        "POST",
        "/api/v1/candidate/audio-recordings/" + str(recording_id) + "/cancel",
        state["headers"],
    )
    assert cancelled["status"] == "CANCELLED"
    assert audio.storage.get(old_stage.storage_key) == content
    replacement = audio.record(state)
    accepted = audio.finalize(state, replacement)
    assert accepted["validation_provenance"]["complete_input"] is True
    assert audio.attach(state, accepted)["revision"] == 1


def test_existing_legacy_ready_replays_preserve_immutable_receipt_and_asset(audio):
    state = audio.start()
    recording = audio.record(state)
    asset = audio.finalize(state, recording)
    asset_id = uuid.UUID(asset["media_asset_id"])
    recording_id = uuid.UUID(recording["recording_session_id"])
    legacy_proof = dict(asset["validation_provenance"])
    for field in (
        "complete_input",
        "decoded_frame_count",
        "inspected_frame_count",
        "decoded_presentation_span_ms",
        "original_presentation_span_ms",
        "original_first_pts_seconds",
        "original_last_end_seconds",
        "timestamp_resolution_seconds",
        "timestamp_rounding_tolerance",
    ):
        legacy_proof.pop(field, None)
    # A test-only old-schema/provenance snapshot represents existing historical
    # READY data; application replay must not relabel or rewrite that history.
    with audio.factory.kw["bind"].begin() as connection:
        for model, identity in (
            (CandidateAudioRecordingSession, recording_id),
            (CandidateMediaAsset, asset_id),
        ):
            table = model.__table__
            connection.execute(
                table.update()
                .where(table.c.id == identity)
                .values(validation_provenance=legacy_proof)
            )
    audio.session.expire_all()
    assert audio.finalize(state, recording)["media_asset_id"] == str(asset_id)
    replay = audio.call(
        "POST",
        "/api/v1/candidate/audio-recordings/" + str(recording_id) + "/recover",
        state["headers"],
    )
    assert replay["asset"]["media_asset_id"] == str(asset_id)
    audio.session.expire_all()
    assert (
        audio.session.get(CandidateAudioRecordingSession, recording_id).validation_provenance
        == legacy_proof
    )
    assert audio.asset(asset).validation_provenance == legacy_proof
    assert len(audio.evidence(state)["recordings"]) == 1


def _legacy_ready_gap_fixture(audio, recording, asset):
    """Represent pre-correction READY history using only isolated fixture SQL."""
    row = audio.asset(asset)
    content = technical_encoded_audio(audio.settings, duration_seconds=40, timestamp_gap_seconds=60)
    digest = hashlib.sha256(content).hexdigest()
    proof = {
        "validator": "ffprobe_and_complete_ffmpeg_audio_decode",
        "duration_source": "decoded_pcm_frame_samples",
        "duration_ms": 1054,
        "source_sha256": digest,
        "source_byte_size": len(content),
    }
    # This real object and receipt reproduce already-READY data accepted by the
    # old prefix decoder. Current application code cannot create this fixture.
    audio.storage.put(row.storage_key, content, "audio/webm;codecs=opus")
    with audio.factory.kw["bind"].begin() as connection:
        table = CandidateAudioRecordingSession.__table__
        connection.execute(
            table.update()
            .where(table.c.id == uuid.UUID(recording["recording_session_id"]))
            .values(
                upload_sha256=digest,
                upload_byte_size=len(content),
                upload_content_type="audio/webm;codecs=opus",
                verified_duration_ms=1054,
                validation_provenance=proof,
            )
        )
        table = CandidateMediaAsset.__table__
        connection.execute(
            table.update()
            .where(table.c.id == row.id)
            .values(
                sha256=digest,
                byte_size=len(content),
                content_type="audio/webm;codecs=opus",
                duration_ms=1054,
                validation_provenance=proof,
            )
        )
    audio.session.expire_all()
    return content, proof


def test_legacy_ready_unattached_cannot_create_new_authoritative_binding(audio):
    state = audio.start()
    recording = audio.record(state)
    asset = audio.finalize(state, recording)
    content, proof = _legacy_ready_gap_fixture(audio, recording, asset)
    rejected = audio.attach(state, asset, expected=409)
    assert "complete-input validation" in rejected["detail"]
    _assert_no_response_attachment(audio, state)
    original = audio.asset(asset)
    assert original.status is CandidateMediaStatus.READY
    assert original.validation_provenance == proof
    assert original.duration_ms == 1054
    assert audio.storage.get(original.storage_key) == content
    cancelled = audio.call(
        "POST",
        "/api/v1/candidate/audio-recordings/" + recording["recording_session_id"] + "/cancel",
        state["headers"],
    )
    assert cancelled["status"] == "CANCELLED"
    fresh = audio.finalize(state, audio.record(state))
    assert audio.attach(state, fresh)["revision"] == 1


def test_legacy_attached_pending_attempt_requires_fully_validated_successor_before_submission(
    audio,
):
    state = audio.start()
    recording = audio.record(state)
    asset = audio.finalize(state, recording)
    saved = audio.attach(state, asset)
    second = audio.finalize(state, audio.record(state, 1))
    audio.attach(state, second, 1)
    content, proof = _legacy_ready_gap_fixture(audio, recording, asset)
    rejected = audio.call("POST", state["path"] + "/submit", state["headers"], expected=409)
    assert "complete-input validation" in rejected["detail"]
    audio.session.expire_all()
    assert (
        audio.session.get(CandidateExamAttempt, uuid.UUID(state["id"])).status.value
        == "IN_PROGRESS"
    )
    previous_revision = audio.session.scalar(
        select(CandidateResponseRevision).where(
            CandidateResponseRevision.attempt_id == uuid.UUID(state["id"]),
            CandidateResponseRevision.payload_checksum
            == _payload_checksum({"media_asset_id": asset["media_asset_id"]}),
        )
    )
    assert previous_revision.revision_number == saved["revision"] == 1
    previous_id, previous_payload = previous_revision.id, dict(previous_revision.payload)
    fresh = audio.finalize(state, audio.record(state))
    assert audio.attach(state, fresh, expected_revision=1)["revision"] == 2
    assert audio.call("POST", state["path"] + "/submit", state["headers"])["status"] == "SUBMITTED"
    audio.session.expire_all()
    assert audio.session.get(CandidateResponseRevision, previous_id).payload == previous_payload
    original = audio.asset(asset)
    assert original.validation_provenance == proof
    assert audio.storage.get(original.storage_key) == content


def test_already_submitted_legacy_ready_history_and_committed_operations_replay_unchanged(audio):
    state = audio.start()
    recording = audio.record(state)
    asset = audio.finalize(state, recording)
    operation = str(uuid.uuid4())
    saved = audio.attach(state, asset, operation=operation)
    second = audio.finalize(state, audio.record(state, 1))
    audio.attach(state, second, 1)
    submitted = audio.call("POST", state["path"] + "/submit", state["headers"])
    content, proof = _legacy_ready_gap_fixture(audio, recording, asset)
    replayed = audio.call("POST", state["path"] + "/submit", state["headers"])
    assert {key: value for key, value in replayed.items() if key != "submitted_at"} == {
        key: value for key, value in submitted.items() if key != "submitted_at"
    }
    assert datetime.fromisoformat(
        replayed["submitted_at"].replace("Z", "+00:00")
    ) == datetime.fromisoformat(submitted["submitted_at"].replace("Z", "+00:00"))
    assert audio.attach(state, asset, operation=operation) == saved
    assert (
        audio.finalize(state, recording, content=content, mime="audio/webm;codecs=opus")[
            "media_asset_id"
        ]
        == asset["media_asset_id"]
    )
    recovered = audio.call(
        "POST",
        "/api/v1/candidate/audio-recordings/" + recording["recording_session_id"] + "/recover",
        state["headers"],
    )
    assert recovered["asset"]["media_asset_id"] == asset["media_asset_id"]
    audio.session.expire_all()
    original = audio.asset(asset)
    receipt = audio.session.get(
        CandidateAudioRecordingSession, uuid.UUID(recording["recording_session_id"])
    )
    assert original.validation_provenance == receipt.validation_provenance == proof
    assert original.duration_ms == receipt.verified_duration_ms == 1054
    assert audio.storage.get(original.storage_key) == content
    assert len(audio.call("GET", state["path"] + "/runtime", state["headers"])["responses"]) == 2


def test_submission_overlaps_actual_private_put_and_cannot_commit_unattached_evidence(
    audio, monkeypatch
):
    state = audio.start()
    second = audio.finalize(state, audio.record(state, 1))
    audio.attach(state, second, 1)
    recording = audio.record(state)
    uploaded, release = Event(), Event()
    original_put = S3CompatibleObjectStorage.put

    def actual_put_then_pause(storage, key, content, mime):
        original_put(storage, key, content, mime)
        uploaded.set()
        assert release.wait(timeout=12), "bounded concurrency release was not signalled"

    independent_submit = audio.login(audio.users[0].email)
    with monkeypatch.context() as patch:
        patch.setattr(S3CompatibleObjectStorage, "put", actual_put_then_pause)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(audio.finalize, state, recording)
            try:
                assert uploaded.wait(timeout=12), "real private upload did not complete"
                audio.call("POST", state["path"] + "/submit", independent_submit, expected=409)
                audio.session.expire_all()
                attempt = audio.session.get(CandidateExamAttempt, uuid.UUID(state["id"]))
                assert attempt.status.value == "IN_PROGRESS"
            finally:
                release.set()
            asset = future.result(timeout=20)
    audio.attach(state, asset)
    audio.call("POST", state["path"] + "/submit", independent_submit)


def test_database_asset_missing_private_object_fails_closed_without_replacing_history(audio):
    state = audio.start()
    recording = audio.record(state)
    asset = audio.finalize(state, recording)
    audio.attach(state, asset)
    row = audio.asset(asset)
    key, digest = row.storage_key, row.sha256
    # Only this isolated test-owned object is removed to simulate external object loss.
    audio.storage.client.delete_object(Bucket=audio.storage.bucket, Key=key)
    audio.call(
        "POST",
        "/api/v1/candidate/audio-recordings/" + recording["recording_session_id"] + "/recover",
        state["headers"],
        expected=503,
    )
    audio.finalize(state, recording, expected=503)
    audio.session.expire_all()
    persisted = audio.asset(asset)
    assert persisted.storage_key == key and persisted.sha256 == digest
    assert persisted.id == uuid.UUID(asset["media_asset_id"])
    assert (
        audio.session.scalar(
            select(func.count())
            .select_from(CandidateResponseMediaBinding)
            .where(CandidateResponseMediaBinding.candidate_media_asset_id == persisted.id)
        )
        == 1
    )


def _fixture_ffmpeg(
    settings: Settings, arguments: list[str], content: bytes | None = None
) -> bytes:
    """Encode original technical fixtures only; product evidence is never transcoded."""
    command = [settings.audio_ffmpeg_executable]
    if settings.audio_media_runtime == "docker":
        command = [
            "docker",
            "run",
            "--interactive",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=32",
            "--cpus=1",
            "--memory=512m",
            "--entrypoint",
            "/usr/bin/ffmpeg",
            settings.audio_media_image,
        ]
    completed = subprocess.run(
        command + ["-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1", *arguments],
        input=content,
        capture_output=True,
        timeout=45,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")[:1000]
    assert 0 < len(completed.stdout) < 2 * 1024 * 1024
    return completed.stdout


@pytest.mark.parametrize(
    "mime,container,codec,encoding",
    [
        ("audio/wav", "wav", "pcm_s16le", []),
        ("audio/webm;codecs=opus", "webm", "opus", ["-c:a", "libopus", "-f", "webm"]),
        ("audio/ogg;codecs=opus", "ogg", "opus", ["-c:a", "libopus", "-f", "ogg"]),
        (
            "audio/mp4;codecs=mp4a.40.2",
            "mp4",
            "aac",
            ["-c:a", "aac", "-movflags", "frag_keyframe+empty_moov", "-f", "mp4"],
        ),
    ],
)
def test_real_declared_container_codec_is_decoded_and_original_bytes_preserved(
    audio, mime, container, codec, encoding
):
    content = technical_tone_wav()
    if encoding:
        content = _fixture_ffmpeg(audio.settings, ["-i", "pipe:0", *encoding, "pipe:1"], content)
    state = audio.start()
    asset = audio.finalize(state, audio.record(state), content=content, mime=mime)
    assert 900 <= asset["duration_ms"] <= 1200  # Real codec priming/padding, not a client duration.
    assert asset["validation_provenance"]["container"] == container
    assert asset["validation_provenance"]["codec"] == codec
    assert asset["sha256"] == hashlib.sha256(content).hexdigest()
    row = audio.asset(asset)
    assert audio.storage.get(row.storage_key) == content


def test_real_policy_length_decode_folds_large_frame_metadata_without_losing_duration(audio):
    content = _fixture_ffmpeg(
        audio.settings,
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "180",
            "-c:a",
            "libopus",
            "-b:a",
            "16k",
            "-f",
            "webm",
            "pipe:1",
        ],
    )
    metadata = _fixture_ffmpeg(
        audio.settings,
        ["-i", "pipe:0", "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "framecrc", "pipe:1"],
        content,
    )
    assert len(metadata) > MAX_INSPECTION_OUTPUT
    validated = validate_recorded_media(
        content,
        "audio/webm;codecs=opus",
        {**AUDIO_POLICY, "max_recording_seconds": 180},
        settings=audio.settings,
    )
    assert validated.duration_ms == 180_000
    assert validated.sha256 == hashlib.sha256(content).hexdigest()
    assert validated.validation_provenance["duration_source"] == "decoded_pcm_frame_samples"


def test_alembic_upgrade_preserves_legacy_owned_asset_binding_object_and_history(audio):
    """Only a newly allocated DB is migrated; no existing database is reset."""
    base_url = make_url(os.environ["POSTGRES_INTEGRATION_URL"])
    database_name = "audio_migration_" + uuid.uuid4().hex
    connection_url = base_url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(connection_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    legacy_url = base_url.set(database=database_name).render_as_string(hide_password=False)
    service_root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "DATABASE_URL": legacy_url, "PYTHONPATH": str(service_root)}

    def migrate(target):
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", target],
            cwd=service_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert completed.returncode == 0, completed.stderr

    migrate("0025_writing_human_review")
    engine = create_engine(legacy_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    payload = technical_tone_wav()
    digest = hashlib.sha256(payload).hexdigest()
    recording_id, asset_id, binding_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    storage_key = "integration/legacy-owned-audio/" + asset_id.hex
    audio.storage.put(storage_key, payload, "audio/wav")
    with session_factory() as session:
        # Build only original AUDIO prerequisites at the legacy schema. The full
        # demo's current objective submit service correctly requires the current
        # schema and is not a historical migration fixture.
        settings = Settings(_env_file=None, environment="development")
        seed_defaults(session)
        operator = _platform_user(session, settings, PASSWORD)
        organisation = Organisation(
            slug="legacy-audio",
            name="Owned legacy migration fixture",
            organisation_type=OrganisationType.LANGUAGE_SCHOOL,
        )
        session.add(organisation)
        session.flush()
        users = _users(session, settings, organisation, PASSWORD)
        admin = users["admin@demo.prufai.local"]
        ids = reconcile_audio_demo(
            session,
            settings,
            enabled=True,
            organisation=organisation,
            administrator=admin,
            platform_operator=operator,
            password=PASSWORD,
        )
        org_id = organisation.id
        candidate_id = uuid.UUID(ids["audio_candidate_user_id"])
        administrator = build_tenant_context(session, admin.id, org_id)
        candidate = build_tenant_context(session, candidate_id, org_id)
        assignment = CandidateRuntimeService.create_assignment(
            session,
            administrator,
            candidate_user_id=candidate_id,
            exam_version_id=uuid.UUID(ids["audio_exam_version_id"]),
        )
        attempt = CandidateRuntimeService.create_attempt(session, candidate, assignment.id)
        CandidateRuntimeService.start(session, candidate, attempt.id)
        item = attempt.runtime_plan["modules"][0]["sections"][0]["tasks"][0]["items"][0]
        item_binding = uuid.UUID(item["binding_id"])
        now = datetime.now(timezone.utc)
        session.execute(
            text("""INSERT INTO candidate_audio_recording_sessions
            (id,attempt_id,exam_version_item_binding_id,candidate_user_id,organisation_id,
             attempt_number,recording_started_at,recording_expires_at,status,operation_id,policy_snapshot)
            VALUES (:id,:attempt,:binding,:candidate,:org,1,:now,:now,'READY',:operation,'{}')"""),
            {
                "id": recording_id,
                "attempt": attempt.id,
                "binding": item_binding,
                "candidate": candidate_id,
                "org": org_id,
                "now": now,
                "operation": uuid.uuid4(),
            },
        )
        session.execute(
            text("""INSERT INTO candidate_media_assets
            (id,organisation_id,candidate_user_id,attempt_id,exam_version_item_binding_id,
             recording_session_id,content_type,byte_size,sha256,duration_ms,storage_key,status,created_at)
            VALUES (:id,:org,:candidate,:attempt,:binding,:recording,'audio/wav',
                    :size,:sha,1000,:key,'READY',:now)"""),
            {
                "id": asset_id,
                "org": org_id,
                "candidate": candidate_id,
                "attempt": attempt.id,
                "binding": item_binding,
                "recording": recording_id,
                "size": len(payload),
                "sha": digest,
                "key": storage_key,
                "now": now,
            },
        )
        response_payload = {"media_asset_id": str(asset_id)}
        response = CandidateResponse(
            attempt_id=attempt.id,
            exam_version_item_binding_id=item_binding,
            current_revision=1,
            current_payload=response_payload,
            first_saved_at=now,
            last_saved_at=now,
        )
        session.add(response)
        session.flush()
        revision = CandidateResponseRevision(
            candidate_response_id=response.id,
            attempt_id=attempt.id,
            revision_number=1,
            client_operation_id=uuid.uuid4(),
            payload=response_payload,
            payload_checksum=_payload_checksum(response_payload),
            received_at=now,
        )
        session.add(revision)
        session.flush()
        session.execute(
            text("""INSERT INTO candidate_response_media_bindings
            (id,candidate_response_revision_id,candidate_media_asset_id,binding_role,created_at)
            VALUES (:id,:revision,:asset,'PRIMARY_AUDIO',:now)"""),
            {"id": binding_id, "revision": revision.id, "asset": asset_id, "now": now},
        )
        session.commit()
        before = (attempt.id, revision.id, revision.payload_checksum, response_payload)
    engine.dispose()
    migrate("head")
    engine = create_engine(legacy_url)
    try:
        with Session(engine) as session:
            assert (
                session.scalar(text("SELECT version_num FROM alembic_version"))
                == repository_alembic_head()
            )
            asset = session.get(CandidateMediaAsset, asset_id)
            recording = session.get(CandidateAudioRecordingSession, recording_id)
            revision = session.get(CandidateResponseRevision, before[1])
            binding = session.get(CandidateResponseMediaBinding, binding_id)
            assert (
                asset.attempt_id,
                revision.id,
                revision.payload_checksum,
                revision.payload,
            ) == before
            assert (
                asset.sha256 == digest
                and asset.byte_size == len(payload)
                and asset.duration_ms == 1000
            )
            assert asset.storage_key == recording.storage_key == storage_key
            assert asset.validation_provenance == recording.validation_provenance == {}
            assert recording.evidence_identity == {} and recording.upload_sha256 is None
            assert (
                binding.candidate_media_asset_id == asset.id
                and binding.candidate_response_revision_id == revision.id
            )
            assert audio.storage.get(asset.storage_key) == payload
            uniques = (
                session.execute(
                    text("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid='candidate_media_assets'::regclass AND contype='u'""")
                )
                .scalars()
                .all()
            )
            assert "UNIQUE (sha256)" not in uniques
            assert "UNIQUE (storage_key)" in uniques and "UNIQUE (recording_session_id)" in uniques
    finally:
        engine.dispose()
