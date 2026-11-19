"""Real HTTP boundaries never serialize driver diagnostics or SQL parameters."""

import json
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import errors
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError

from app.auth.api import get_principal
from app.authorization import AuthorizationError
from app.db import get_db_session
from app.result_release.api import candidate_router as candidate_release_router
from app.result_release.api import router as release_router
from app.result_release.service import ResultReleaseError
from app.runtime.api import candidate_router
from app.scoring.api import scoring_router
from app.scoring.service import ScoringError

SECRET = "synthetic-db-parameter-never-public"
SQL = "SELECT private_column FROM private_table WHERE token = :secret"


@pytest.mark.parametrize("boundary", ["review", "writer", "statement", "release"])
@pytest.mark.parametrize(
    "original,status,category",
    [
        (errors.DeadlockDetected(SECRET), 503, "DATABASE_CONFLICT"),
        (errors.SerializationFailure(SECRET), 503, "DATABASE_CONFLICT"),
        (errors.LockNotAvailable(SECRET), 503, "DATABASE_CONFLICT"),
        (errors.QueryCanceled(SECRET), 503, "DATABASE_TIMEOUT"),
        (errors.AdminShutdown(SECRET), 503, "TEMPORARILY_UNAVAILABLE"),
        (errors.UndefinedTable(SECRET), 500, "INTERNAL_ERROR"),
        (errors.UniqueViolation(SECRET), 500, "INTERNAL_ERROR"),
    ],
)
def test_database_failures_are_sanitized_and_correlated(
    monkeypatch, caplog, boundary, original, status, category
):
    failure = OperationalError(SQL, {"secret": SECRET}, original)
    _assert_failure(monkeypatch, caplog, boundary, failure, status, category)


@pytest.mark.parametrize("boundary", ["review", "writer", "statement", "release"])
@pytest.mark.parametrize(
    "failure",
    [
        ProgrammingError(SQL, {"secret": SECRET}, errors.UndefinedTable(SECRET)),
        SQLAlchemyError(SECRET),
    ],
)
def test_unclassified_database_errors_are_not_reported_as_outages(
    monkeypatch, caplog, boundary, failure
):
    _assert_failure(monkeypatch, caplog, boundary, failure, 500, "INTERNAL_ERROR")


def _request(monkeypatch, boundary, failure):
    org, attempt = uuid.uuid4(), uuid.uuid4()
    application = FastAPI()
    application.include_router(scoring_router)
    application.include_router(candidate_router)
    application.include_router(candidate_release_router)
    application.include_router(release_router)
    session = Mock()
    application.dependency_overrides[get_db_session] = lambda: session
    application.dependency_overrides[get_principal] = lambda: SimpleNamespace(
        tenant_context=SimpleNamespace(organisation_id=org)
    )

    def fail(*_args, **_kwargs):
        raise failure

    if boundary == "review":
        monkeypatch.setattr("app.scoring.api.ScoringService.request_review", fail)
        path = f"/api/v1/organisations/{org}/result-versions/{uuid.uuid4()}/request-review"
        method = "POST"
    elif boundary == "writer":
        monkeypatch.setattr("app.runtime.api.CandidateRuntimeService._owned_attempt", fail)
        path = f"/api/v1/candidate/exam-attempts/{attempt}/writer"
        method = "GET"
    elif boundary == "statement":
        monkeypatch.setattr("app.result_release.api._candidate_release", fail)
        path = f"/api/v1/candidate/releases/{uuid.uuid4()}/statement"
        method = "GET"
    else:
        monkeypatch.setattr("app.result_release.api.ResultReleaseService.statement", fail)
        path = f"/api/v1/organisations/{org}/releases/{uuid.uuid4()}/statement"
        method = "POST"
    with TestClient(application) as client:
        response = client.request(
            method,
            path,
            **({"json": {"operation_id": "synthetic-error"}} if boundary == "release" else {}),
        )
    session.rollback.assert_called_once()
    return response


def _assert_failure(monkeypatch, caplog, boundary, failure, status, category):
    with caplog.at_level(logging.WARNING, logger="prufai.operations"):
        response = _request(monkeypatch, boundary, failure)
    prefix = {"review": "SCORING", "writer": "RUNTIME"}.get(boundary, "RESULT_RELEASE")
    assert response.status_code == status
    assert response.json() == {"detail": f"{prefix}.{category}"}
    request_id = response.headers["x-request-id"]
    assert str(uuid.UUID(request_id)) == request_id
    assert response.headers["cache-control"] == "no-store"
    receipts = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "prufai.operations"
    ]
    assert receipts == [
        {
            "event": "database_request_failed",
            "operation": prefix,
            "failure_category": category,
            "request_id": request_id,
            "status_code": status,
        }
    ]
    for forbidden in (SECRET, SQL, "private_column", "private_table", "sqlalchemy", "parameters"):
        assert forbidden not in response.text
        assert forbidden not in caplog.text


def test_review_domain_conflict_keeps_409_and_is_not_a_database_outage(monkeypatch, caplog):
    response = _request(monkeypatch, "review", ScoringError("result is not awaiting review"))
    assert response.status_code == 409
    assert response.json() == {"detail": "result is not awaiting review"}
    assert "x-request-id" not in response.headers
    assert not caplog.records


@pytest.mark.parametrize("boundary,prefix", [("review", "SCORING"), ("release", "RESULT_RELEASE")])
def test_arbitrary_application_error_is_not_relabelled_as_database_outage(
    monkeypatch, caplog, boundary, prefix
):
    response = _request(monkeypatch, boundary, RuntimeError(SECRET))
    assert response.status_code == 500
    assert response.json() == {"detail": f"{prefix}.INTERNAL_ERROR"}
    assert SECRET not in response.text
    assert not caplog.records


@pytest.mark.parametrize(
    "failure,status",
    [
        (ResultReleaseError("release is not active or is stale"), 409),
        (AuthorizationError("result.release required"), 403),
    ],
)
def test_release_business_and_authorization_errors_are_preserved(
    monkeypatch, caplog, failure, status
):
    response = _request(monkeypatch, "release", failure)
    assert response.status_code == status
    assert response.json() == {"detail": str(failure)}
    assert "x-request-id" not in response.headers
    assert not caplog.records
