"""Required migration-backed, separate-session LiD writer fencing evidence."""

import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select, text

from app.authorization import build_tenant_context
from app.models import AuditEvent, CandidateExamAttempt, CandidateResponseRevision
from app.runtime.service import CandidateRuntimeService, RuntimeConflict
from app.speaking_audio.service import SpeakingAudioService
from tests.test_lid_practice_postgresql_minio_integration import (
    CANDIDATE,
    _assignment,
    _items,
    _prepare,
)
from tests.test_lid_practice_postgresql_minio_integration import (
    configured as configured,
)
from tests.test_lid_practice_postgresql_minio_integration import (
    expert as expert,
)
from tests.test_lid_practice_postgresql_minio_integration import practice as practice
from tests.test_lid_practice_postgresql_minio_integration import proof as proof


def _claim(proof, attempt, client, generation, *, ack=False, status=200, actor="candidate"):
    return proof.http(
        "POST",
        CANDIDATE + f"/exam-attempts/{attempt}/writer",
        actor=actor,
        status=status,
        json={
            "client_id": str(client),
            "expected_generation": generation,
            "acknowledge_takeover": ack,
        },
    )


def _headers(client, generation):
    return {"X-PrufAI-Writer-ID": str(client), "X-PrufAI-Writer-Generation": str(generation)}


def _new(proof, practice):
    attempt = _prepare(proof, _assignment(proof, practice))["id"]
    marker = uuid.uuid4()
    assert _claim(proof, attempt, marker, 0)["generation"] == 1
    state = proof.http(
        "POST",
        CANDIDATE + f"/exam-attempts/{attempt}/start",
        actor="candidate",
        extra_headers=_headers(marker, 1),
    )
    return attempt, marker, _items(state)[0]


def _save(
    proof, attempt, item, marker, generation, revision, *, operation=None, option=0, status=200
):
    return proof.http(
        "PUT",
        CANDIDATE + f"/exam-attempts/{attempt}/responses/{item['binding_id']}",
        actor="candidate",
        status=status,
        extra_headers=_headers(marker, generation) if marker else {},
        json={
            "client_operation_id": str(operation or uuid.uuid4()),
            "expected_revision": revision,
            "payload": {"option_id": item["content"]["options"][option]["option_id"]},
        },
    )


def test_default_deny_then_explicit_takeover_preserves_exact_answer_history(proof, practice):
    attempt, first, item = _new(proof, practice)
    operation = uuid.uuid4()
    assert _save(proof, attempt, item, first, 1, 0, operation=operation)["revision"] == 1
    other = uuid.uuid4()
    for path in ("submit", "navigate?binding_id=" + item["binding_id"]):
        response = proof.http(
            "POST", CANDIDATE + f"/exam-attempts/{attempt}/" + path, actor="candidate", status=409
        )
        assert response["detail"] == "RUNTIME.WRITER_CONTEXT_REQUIRED"
    assert (
        _claim(proof, attempt, other, 1, status=409)["detail"]
        == "RUNTIME.WRITER_CONFIRMATION_REQUIRED"
    )
    assert _claim(proof, attempt, other, 1, ack=True)["generation"] == 2
    assert (
        _save(proof, attempt, item, first, 1, 1, option=1, status=409)["detail"]
        == "RUNTIME.WRITER_CHANGED"
    )
    replay = _save(proof, attempt, item, other, 2, 0, operation=operation)
    assert replay["revision"] == 1
    assert _save(proof, attempt, item, other, 2, 1, option=1)["revision"] == 2
    endpoint = CANDIDATE + f"/exam-attempts/{attempt}/submit"
    result = proof.http("POST", endpoint, actor="candidate", extra_headers=_headers(other, 2))
    repeated = proof.http("POST", endpoint, actor="candidate", extra_headers=_headers(other, 2))
    assert result["status"] == repeated["status"] == "SUBMITTED"
    with proof.expert.factory() as session:
        history = list(
            session.scalars(
                select(CandidateResponseRevision)
                .where(CandidateResponseRevision.attempt_id == uuid.UUID(attempt))
                .order_by(CandidateResponseRevision.revision_number)
            )
        )
        assert len(history) == 2 and history[0].client_operation_id == operation
        assert history[0].payload != history[1].payload
        receipts = list(session.scalars(select(AuditEvent).where(AuditEvent.entity_id == attempt)))
        writer_events = [e for e in receipts if e.event_type.startswith("exam_attempt.writer_")]
        assert len(writer_events) == 2
        safe = json.dumps([e.metadata_json for e in writer_events], default=str)
        for private in (
            str(first),
            str(other),
            hashlib.sha256(str(first).encode()).hexdigest(),
            hashlib.sha256(str(other).encode()).hexdigest(),
        ):
            assert private not in safe


def test_two_simultaneous_takeovers_cannot_both_win_one_generation(proof, practice):
    attempt, _first, _item = _new(proof, practice)
    barrier = threading.Barrier(2)
    markers = [uuid.uuid4(), uuid.uuid4()]

    def acquire(marker):
        barrier.wait(timeout=10)
        return marker, _claim(proof, attempt, marker, 1, ack=True, status=(200, 409))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, markers))
    winners = [(marker, reply) for marker, reply in results if reply.get("generation") == 2]
    assert len(winners) == 1
    assert sum(reply.get("detail") == "RUNTIME.WRITER_CHANGED" for _marker, reply in results) == 1
    with proof.expert.factory() as session:
        stored = session.get(CandidateExamAttempt, uuid.UUID(attempt))
        assert stored.writer_generation == 2
        assert stored.writer_client_hash == hashlib.sha256(str(winners[0][0]).encode()).hexdigest()


def test_takeover_waits_for_inflight_save_and_refreshes_cached_orm_authority(proof, practice):
    attempt, first, item = _new(proof, practice)
    assert _save(proof, attempt, item, first, 1, 0)["revision"] == 1
    other = uuid.uuid4()
    pid_ready = threading.Event()
    process_id = []
    actor = proof.expert.actors["candidate"]
    org = uuid.UUID(practice[0])

    def takeover():
        with proof.expert.factory() as session:
            context = build_tenant_context(session, actor, org)
            process_id.append(session.scalar(text("SELECT pg_backend_pid()")))
            pid_ready.set()
            result = CandidateRuntimeService.claim_writer(
                session,
                context,
                uuid.UUID(attempt),
                client_id=other,
                expected_generation=1,
                acknowledge_takeover=True,
            )
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=1) as pool:
        with proof.expert.factory() as session:
            context = build_tenant_context(session, actor, org)
            cached = SpeakingAudioService.lock_attempt(session, context, uuid.UUID(attempt))
            future = pool.submit(takeover)
            try:
                assert pid_ready.wait(timeout=10)
                deadline = time.monotonic() + 5
                waiting = False
                while time.monotonic() < deadline and not future.done():
                    with proof.expert.factory() as observer:
                        waiting = (
                            observer.scalar(
                                text(
                                    "SELECT wait_event_type = 'Lock' "
                                    "FROM pg_stat_activity WHERE pid = :pid"
                                ),
                                {"pid": process_id[0]},
                            )
                            is True
                        )
                    if waiting:
                        break
                    pid_ready.wait(timeout=0.01)
                assert waiting, "The second PostgreSQL session never waited for the attempt lock"
                assert cached.writer_generation == 1
                CandidateRuntimeService.save_response(
                    session,
                    context,
                    uuid.UUID(attempt),
                    uuid.UUID(item["binding_id"]),
                    payload={"option_id": item["content"]["options"][1]["option_id"]},
                    client_operation_id=uuid.uuid4(),
                    expected_revision=1,
                    writer_id=first,
                    writer_generation=1,
                )
                session.commit()
                assert future.result(timeout=10)["generation"] == 2
                try:
                    CandidateRuntimeService.save_response(
                        session,
                        context,
                        uuid.UUID(attempt),
                        uuid.UUID(item["binding_id"]),
                        payload={"option_id": item["content"]["options"][0]["option_id"]},
                        client_operation_id=uuid.uuid4(),
                        expected_revision=2,
                        writer_id=first,
                        writer_generation=1,
                    )
                except RuntimeConflict as exc:
                    assert str(exc) == "RUNTIME.WRITER_CHANGED"
                else:
                    raise AssertionError("A stale ORM identity bypassed the new writer generation")
            finally:
                session.rollback()
    fresh = proof.http("GET", CANDIDATE + f"/exam-attempts/{attempt}/runtime", actor="candidate")
    assert fresh["responses"][0]["revision"] == 2


def test_writer_ownership_and_confirmation_survive_new_database_sessions(proof, practice):
    attempt, first, _item = _new(proof, practice)
    before = proof.http("GET", CANDIDATE + f"/exam-attempts/{attempt}/runtime", actor="candidate")
    assert str(first) not in json.dumps(before)
    assert _claim(proof, attempt, first, 0)["generation"] == 1
    assert (
        _claim(proof, attempt, uuid.uuid4(), 1, status=409)["detail"]
        == "RUNTIME.WRITER_CONFIRMATION_REQUIRED"
    )
    after = proof.http("GET", CANDIDATE + f"/exam-attempts/{attempt}/runtime", actor="candidate")
    assert before["deadline_at"] == after["deadline_at"]
    assert before["runtime_plan_checksum"] == after["runtime_plan_checksum"]


def test_nonowners_and_platform_identities_cannot_claim_or_transfer(proof, practice):
    attempt, first, _item = _new(proof, practice)
    for actor in ("outsider", "revoked", "creator"):
        _claim(proof, attempt, uuid.uuid4(), 1, ack=True, status=403, actor=actor)
    assert _claim(proof, attempt, first, 0)["generation"] == 1


def test_expired_attempt_cannot_reopen_or_transfer_authority(proof, practice, monkeypatch):
    attempt, first, _item = _new(proof, practice)
    with proof.expert.factory() as session:
        row = session.get(CandidateExamAttempt, uuid.UUID(attempt))
        deadline = row.deadline_at
    monkeypatch.setattr("app.runtime.service._now", lambda: deadline + timedelta(seconds=1))
    response = _claim(proof, attempt, uuid.uuid4(), 1, ack=True, status=409)
    assert response["detail"] == "RUNTIME.ATTEMPT_EXPIRED"
    state = proof.http("GET", CANDIDATE + f"/exam-attempts/{attempt}/runtime", actor="candidate")
    assert state["status"] == "EXPIRED" and state["writer"]["generation"] == 1


def test_concurrent_submit_and_takeover_have_only_one_authorized_outcome(proof, practice):
    attempt, first, item = _new(proof, practice)
    assert _save(proof, attempt, item, first, 1, 0)["revision"] == 1
    barrier = threading.Barrier(2)
    other = uuid.uuid4()

    def transfer():
        barrier.wait(timeout=10)
        return _claim(proof, attempt, other, 1, ack=True, status=(200, 409))

    def submit():
        barrier.wait(timeout=10)
        return proof.http(
            "POST",
            CANDIDATE + f"/exam-attempts/{attempt}/submit",
            actor="candidate",
            extra_headers=_headers(first, 1),
            status=(200, 409),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        transfer_result = pool.submit(transfer)
        submit_result = pool.submit(submit)
        changed, submitted = transfer_result.result(timeout=15), submit_result.result(timeout=15)
    with proof.expert.factory() as session:
        stored = session.get(CandidateExamAttempt, uuid.UUID(attempt))
        if stored.status.value == "SUBMITTED":
            assert stored.writer_generation == 1
            assert submitted["status"] == "SUBMITTED"
            assert changed["detail"] == "attempt is not writable"
        else:
            assert stored.status.value == "IN_PROGRESS" and stored.writer_generation == 2
            assert changed["generation"] == 2
            assert submitted["detail"] == "RUNTIME.WRITER_CHANGED"
