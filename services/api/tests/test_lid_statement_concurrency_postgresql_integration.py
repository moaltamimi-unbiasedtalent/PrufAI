"""Authenticated release reads contend on migration-backed PostgreSQL locks."""

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text

from app.config import get_settings
from app.main import app
from app.models import (
    AuditEvent,
    CandidateResponseRevision,
    ResultRelease,
    ResultVersion,
    ScoringRun,
    StatementOfResults,
    User,
)
from tests.test_expert_references_postgresql_integration import PASSWORD
from tests.test_lid_practice_postgresql_minio_integration import (
    CANDIDATE,
    _release_practice_result,
    _score,
)
from tests.test_lid_practice_postgresql_minio_integration import configured as configured
from tests.test_lid_practice_postgresql_minio_integration import expert as expert
from tests.test_lid_practice_postgresql_minio_integration import practice as practice
from tests.test_lid_practice_postgresql_minio_integration import proof as proof
from tests.test_lid_writer_postgresql_integration import _headers, _new, _save


def _request(method, path, headers, payload=None):
    with TestClient(app) as client:
        return client.request(method, path, headers=headers, json=payload)


def _prepare(proof, practice, monkeypatch):
    attempt, marker, item = _new(proof, practice)
    _save(proof, attempt, item, marker, 1, 0)
    proof.http(
        "POST",
        CANDIDATE + f"/exam-attempts/{attempt}/submit",
        actor="candidate",
        extra_headers=_headers(marker, 1),
    )
    run = _score(proof, practice, attempt)
    assert run["status"] == "COMPLETED"
    release = _release_practice_result(proof, practice[0], attempt, run)
    prefix = f"/api/v1/organisations/{practice[0]}"
    path = CANDIDATE + f"/releases/{release}/statement"
    # Release alone does not invent a statement.
    assert _request("GET", path, proof.headers["candidate"]).status_code == 404
    proof.http(
        "POST",
        prefix + f"/releases/{release}/statement",
        actor="revoked",
        json={"operation_id": str(uuid.uuid4())},
    )
    with proof.expert.factory() as session:
        email = session.get(User, proof.expert.actors["candidate"]).email
        stored = session.get(ResultRelease, release)
        version_id, result_id = stored.result_version_id, stored.assessment_result_id
    # Each candidate request has a real, distinct authentication session, so
    # last_seen updates cannot accidentally serialize the concurrency proof.
    with TestClient(app) as client:
        signed = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert signed.status_code == 200
        writer_headers = {
            "Authorization": "Bearer " + signed.json()["access_token"],
            "X-PrufAI-Organisation-ID": practice[0],
        }
    monkeypatch.setenv("V1_PILOT_PROFILE", "BERLIN_LID_SYNTHETIC")
    monkeypatch.setenv("DATABASE_URL", proof.expert.database_url)
    monkeypatch.setenv("PRUFAI_DISPOSABLE_DATABASE", proof.expert.database_url.rsplit("/", 1)[1])
    get_settings.cache_clear()
    return {
        "attempt": attempt,
        "release": release,
        "version": version_id,
        "result": result_id,
        "run": run,
        "path": path,
        "prefix": prefix,
        "writer_headers": writer_headers,
    }


def _snapshot(proof, state):
    with proof.expert.factory() as session:
        release = session.get(ResultRelease, state["release"])
        version = session.get(ResultVersion, state["version"])
        run = session.get(ScoringRun, uuid.UUID(state["run"]["id"]))
        statement = session.scalar(
            select(StatementOfResults).where(StatementOfResults.release_id == release.id)
        )
        assert run.status.value == "COMPLETED"
        assert version.outcome.value == "UNDETERMINED"
        assert version.scoring_run_id == release.scoring_run_id == run.id
        assert version.scoring_run_checksum == run.checksum
        assert version.response_set_checksum == run.response_set_checksum
        revisions = session.scalar(
            select(func.count())
            .select_from(CandidateResponseRevision)
            .where(CandidateResponseRevision.attempt_id == uuid.UUID(state["attempt"]))
        )
        assert revisions == 1
        return {
            "release_checksum": release.checksum,
            "version_checksum": version.checksum,
            "statement_checksum": statement.checksum,
            "run_checksum": run.checksum,
            "response_checksum": run.response_set_checksum,
        }


def _wait_graph(engine, holder, waiter, done):
    deadline = time.monotonic() + 5
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
        while time.monotonic() < deadline:
            rows = [
                dict(row)
                for row in observer.execute(
                    text(
                        "SELECT pid,wait_event_type,wait_event,pg_blocking_pids(pid) AS blockers "
                        "FROM pg_stat_activity WHERE pid=:pid"
                    ),
                    {"pid": waiter},
                ).mappings()
            ]
            if rows and rows[0]["wait_event_type"] == "Lock" and holder in rows[0]["blockers"]:
                locks = [
                    dict(row)
                    for row in observer.execute(
                        text(
                            "SELECT pid,locktype,mode,granted FROM pg_locks "
                            "WHERE pid=:pid AND NOT granted"
                        ),
                        {"pid": waiter},
                    ).mappings()
                ]
                assert locks
                return {"holder_pid": holder, "waiter": rows[0], "ungranted_locks": locks}
            assert not done.is_set(), (
                "competing request finished without an observed PostgreSQL wait"
            )
            done.wait(0.005)
    pytest.fail("the real PostgreSQL lock wait was not observed")


@pytest.mark.parametrize("action", ["writer", "release", "revoke", "versions"])
@pytest.mark.parametrize("statement_first", [True, False])
def test_statement_and_competing_requests_use_safe_lock_order(
    proof, practice, monkeypatch, action, statement_first
):
    state = _prepare(proof, practice, monkeypatch)
    engine = proof.expert.engine
    held, requested, release_gate, done = (threading.Event() for _ in range(4))
    roles, pids, sqlstates, acquisitions = {}, {}, [], []
    mutex = threading.Lock()
    actions = {
        "holder": "statement" if statement_first else action,
        "contender": action if statement_first else "statement",
    }

    def begin(connection):
        connection.info.pop("statement_test_role", None)
        connection.info["statement_test_attempt"] = False

    def before(connection, cursor, query, parameters, context, many):
        if "FOR UPDATE" not in query:
            return
        resource = next(
            (
                name
                for table, name in [
                    ("candidate_exam_attempts", "attempt"),
                    ("knowledge_document_versions", "sources"),
                    ("assessment_results", "result"),
                    ("result_versions", "version"),
                ]
                if table in query
            ),
            None,
        )
        if resource is None:
            return
        if "statement_test_role" not in connection.info:
            with mutex:
                role = "holder" if not roles else "contender"
                roles[role] = connection
            connection.info["statement_test_role"] = role
            pids[role] = connection.connection.driver_connection.info.backend_pid
            if role == "contender":
                requested.set()
        role = connection.info["statement_test_role"]
        if resource == "sources" and actions[role] != "versions":
            assert connection.info["statement_test_attempt"], (
                "source validation preceded attempt ownership lock"
            )

    def after(connection, cursor, query, parameters, context, many):
        if "FOR UPDATE" not in query:
            return
        resource = (
            "attempt"
            if "candidate_exam_attempts" in query
            else "sources"
            if "knowledge_document_versions" in query
            else None
        )
        if resource is None:
            return
        role = connection.info["statement_test_role"]
        acquisitions.append((role, resource))
        if resource == "attempt":
            connection.info["statement_test_attempt"] = True
        # Version-list reads only lock sources, so let the statement reach that
        # shared resource before holding it. Attempt-only holding would exercise
        # no contention in this direction and must not count as a passing proof.
        hold_resource = "sources" if action == "versions" and statement_first else None
        if (
            role == "holder"
            and not held.is_set()
            and (hold_resource is None or resource == hold_resource)
        ):
            held.set()
            assert release_gate.wait(10), "test coordinator did not release its barrier"

    def database_error(context):
        sqlstates.append(getattr(context.original_exception, "sqlstate", None))

    def perform(kind):
        if kind == "statement":
            return _request("GET", state["path"], proof.headers["candidate"])
        if kind == "writer":
            return _request(
                "GET",
                CANDIDATE + f"/exam-attempts/{state['attempt']}/writer",
                state["writer_headers"],
            )
        if kind == "versions":
            return _request(
                "GET",
                state["prefix"] + f"/results/{state['result']}/versions",
                proof.headers["revoked"],
            )
        if kind == "release":
            return _request(
                "POST",
                state["prefix"] + f"/result-versions/{state['version']}/release",
                proof.headers["revoked"],
                {"operation_id": "lid-practice-release"},
            )
        return _request(
            "POST",
            state["prefix"] + f"/releases/{state['release']}/revoke",
            proof.headers["revoked"],
            {"operation_id": "statement-contention-revoke", "reason": "SYNTHETIC TEST revocation"},
        )

    baseline = _snapshot(proof, state)
    listeners = [
        ("begin", begin),
        ("before_cursor_execute", before),
        ("after_cursor_execute", after),
        ("handle_error", database_error),
    ]
    for name, callback in listeners:
        event.listen(engine, name, callback)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(perform, actions["holder"])
            try:
                assert held.wait(10)

                def compete():
                    try:
                        return perform(actions["contender"])
                    finally:
                        done.set()

                second = pool.submit(compete)
                assert requested.wait(10)
                graph = _wait_graph(engine, pids["holder"], pids["contender"], done)
                release_gate.set()
                responses = {
                    actions["holder"]: first.result(timeout=12),
                    actions["contender"]: second.result(timeout=12),
                }
            finally:
                release_gate.set()
        assert not sqlstates
        assert responses[action].status_code == 200
        expected = 404 if action == "revoke" and not statement_first else 200
        assert responses["statement"].status_code == expected
        assert responses["statement"].headers["cache-control"] == "no-store"
        if expected == 200:
            assert responses["statement"].json()["currently_valid"] is True
        else:
            assert responses["statement"].json() == {"detail": "statement not available"}
        assert _snapshot(proof, state) == baseline
        if action == "versions":
            version = next(
                v
                for v in responses[action].json()
                if v["result_version_id"] == str(state["version"])
            )
            assert version["checksum"] == baseline["version_checksum"]
            assert version["scoring_run_id"] == state["run"]["id"]
        print(
            "STATEMENT_LOCK_CONTENTION = "
            + json.dumps(
                {
                    "action": action,
                    "statement_first": statement_first,
                    "postgres_wait": graph,
                    "sqlstates": sqlstates,
                    "http": {k: v.status_code for k, v in responses.items()},
                }
            )
        )
    finally:
        release_gate.set()
        for name, callback in listeners:
            event.remove(engine, name, callback)
        get_settings.cache_clear()


def test_statement_scope_revocation_and_failed_mutation_preserve_history(
    proof, practice, monkeypatch
):
    state = _prepare(proof, practice, monkeypatch)
    try:
        baseline = _snapshot(proof, state)
        for actor in ("outsider", "revoked"):
            denied = _request("GET", state["path"], proof.headers[actor])
            assert denied.status_code == 404
            assert denied.json() == {"detail": "release not found"}
            assert denied.headers["cache-control"] == "no-store"
        failed = _request(
            "POST",
            state["prefix"] + f"/releases/{state['release']}/revoke",
            proof.headers["revoked"],
            {"reason": " ", "operation_id": "rejected-revocation"},
        )
        assert failed.status_code == 409
        with proof.expert.factory() as session:
            release = session.get(ResultRelease, state["release"])
            assert release.status.value == "RELEASED"
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.entity_id == str(release.id),
                        AuditEvent.event_type == "result.release_revoked",
                    )
                )
                == 0
            )
        assert _snapshot(proof, state) == baseline
        assert _request("GET", state["path"], proof.headers["candidate"]).status_code == 200
        revoked = _request(
            "POST",
            state["prefix"] + f"/releases/{state['release']}/revoke",
            proof.headers["revoked"],
            {"reason": "SYNTHETIC TEST revocation", "operation_id": "explicit-revocation"},
        )
        assert revoked.status_code == 200
        assert _request("GET", state["path"], proof.headers["candidate"]).status_code == 404
        detail = _request(
            "GET", state["path"].removesuffix("/statement"), proof.headers["candidate"]
        )
        assert detail.status_code == 200
        assert detail.json()["effective_status"] == "REVOKED"
        assert not {"result", "statements", "certificates"}.intersection(detail.json())
        assert _snapshot(proof, state) == baseline
        monkeypatch.setenv("V1_PILOT_PROFILE", "BERLIN_LID_LOCKED")
        get_settings.cache_clear()
        assert _request("GET", state["path"], proof.headers["candidate"]).status_code == 404
    finally:
        get_settings.cache_clear()
