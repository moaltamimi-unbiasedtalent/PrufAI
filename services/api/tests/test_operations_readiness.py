from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from psycopg.errors import AdminShutdown, UndefinedTable
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.main import app
from app.operations.readiness import Dependency, Readiness, is_database_unavailable
from app.operations.telemetry import Failure


@pytest.mark.parametrize("status_code", [200, 503])
def test_readiness_is_anonymous_minimal_and_does_not_change_liveness(monkeypatch, status_code):
    healthy = status_code == 200
    readiness = Readiness(
        Dependency("HEALTHY"),
        Dependency("HEALTHY") if healthy else Dependency("UNAVAILABLE", Failure.TIMEOUT),
        datetime.now(timezone.utc),
    )
    monkeypatch.setattr("app.main.measure_readiness", lambda _: readiness)
    with TestClient(app) as client:
        response = client.get("/ready")
        assert response.status_code == status_code
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {"status": "ready" if healthy else "unavailable"}
        assert client.get("/health").status_code == 200


def test_database_outage_classification_excludes_schema_and_programming_errors():
    assert is_database_unavailable(OperationalError("SELECT 1", {}, AdminShutdown()))
    assert not is_database_unavailable(ProgrammingError("SELECT 1", {}, UndefinedTable()))
