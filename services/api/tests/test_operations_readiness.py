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


@pytest.mark.parametrize(
    "challenge_status,marker,expected",
    [
        (200, None, "NOT_VERIFIED"),
        (200, "unsigned-trailer-denied", "NOT_VERIFIED"),
        (403, None, "NOT_VERIFIED"),
        (403, "unsigned-trailer-denied", "VERIFIED"),
    ],
)
def test_private_canary_does_not_establish_storage_guard(challenge_status, marker, expected):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    from app.config import Settings
    from app.operations.readiness import CANARY_BYTES, CANARY_KEY, measure_storage

    class Endpoint(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            assert self.path == "/minio/health/ready"
            if self.headers.get("X-Amz-Content-Sha256") == "STREAMING-UNSIGNED-PAYLOAD-TRAILER":
                self.send_response(challenge_status)
                if marker is not None:
                    self.send_header("X-Prufai-Storage-Guard", marker)
            else:
                self.send_response(200)
            self.end_headers()

        def do_GET(self):
            assert self.path == f"/synthetic/{CANARY_KEY}"
            assert self.headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 ")
            self.send_response(200)
            self.send_header("Content-Length", str(len(CANARY_BYTES)))
            self.end_headers()
            self.wfile.write(CANARY_BYTES)

    with HTTPServer(("127.0.0.1", 0), Endpoint) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            settings = Settings(
                _env_file=None,
                object_storage_backend="s3",
                object_storage_endpoint=f"http://127.0.0.1:{server.server_port}",
                object_storage_bucket="synthetic",
                object_storage_access_key="synthetic",
                object_storage_secret_key="synthetic-fixture-secret",
                operations_storage_guard_probe_enabled=True,
                operations_storage_canary_enabled=True,
            )
            observed = measure_storage(settings).data(settings)
            assert observed["endpoint"] == "REACHABLE"
            assert observed["health"] == "HEALTHY"
            assert observed["canary"] == "VERIFIED"
            assert observed["guard"] == expected
        finally:
            server.shutdown()
            thread.join()
