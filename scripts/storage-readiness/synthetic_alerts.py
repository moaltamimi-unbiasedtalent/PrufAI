"""Private loopback delivery and explicit acknowledgement for the V1 rehearsal."""

from __future__ import annotations

import hmac
import json
import re
import secrets
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from uuid import UUID, uuid4

import httpx

LABELS = {
    "postgresql.readiness": ("postgresql", "HIGH", {"UNAVAILABLE", "TIMEOUT"}),
    "redis.readiness": ("redis", "HIGH", {"UNAVAILABLE", "TIMEOUT"}),
    "storage.guard": ("storage", "HIGH", {"UNAVAILABLE"}),
    "storage.private_operation": ("storage", "HIGH", {"UNAVAILABLE"}),
    "storage.integrity": ("storage", "HIGH", {"INTEGRITY_MISMATCH"}),
    "backup.integrity": ("backup", "HIGH", {"INTEGRITY_MISMATCH"}),
    "restore.reconciliation": ("restore", "HIGH", {"INTEGRITY_MISMATCH"}),
    "worker.activation": ("worker", "HIGH", {"POLICY_DENIED"}),
    "provider.execution": ("provider", "HIGH", {"POLICY_DENIED"}),
    "result_release.operation": ("result_release", "HIGH", {"OPERATION_FAILED"}),
}
MAX_BODY = 2048
MAX_EVENTS = 48
ATTEMPTS = 2


def now():
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class Alert:
    event_code: str
    component: str
    failure_category: str
    severity: str
    observed_at: str
    environment: str
    revision: str
    correlation: str
    recovery_status: str

    @classmethod
    def create(cls, code, failure, revision):
        component, severity, _ = LABELS[code]
        event = cls(
            code,
            component,
            failure,
            severity,
            now(),
            "TEST_ONLY",
            revision,
            str(uuid4()),
            "OPEN",
        )
        event.validate()
        return event

    def validate(self):
        if self.event_code not in LABELS:
            raise ValueError("invalid event code")
        component, severity, failures = LABELS[self.event_code]
        if (
            self.component != component
            or self.severity != severity
            or self.failure_category not in failures
            or self.environment != "TEST_ONLY"
            or self.recovery_status not in {"OPEN", "RESOLVED"}
            or not re.fullmatch(r"[a-f0-9]{40}", self.revision)
            or not re.fullmatch(r"[0-9a-f-]{36}", self.correlation)
            or not re.fullmatch(r"[0-9T:.+-]{29}", self.observed_at)
        ):
            raise ValueError("invalid alert fields")
        if str(UUID(self.correlation, version=4)) != self.correlation:
            raise ValueError("invalid correlation")
        if datetime.fromisoformat(self.observed_at).utcoffset() != UTC.utcoffset(None):
            raise ValueError("timestamp must be UTC")


class SyntheticAlerts:
    def __init__(self, revision, server, token, events, lock):
        self.revision = revision
        self.server = server
        self.token = token
        self.events = events
        self.lock = lock
        self.evidence = []
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=0.5,
            trust_env=False,
            follow_redirects=False,
        )

    def send(self, event):
        payload = asdict(event)
        status = "FAILED"
        deliveries = []
        for attempt in range(1, ATTEMPTS + 1):
            deliveries.append(
                {"status": "ALERT_DELIVERY_ATTEMPTED", "observed_at": now()}
            )
            failure = "INVALID_RECEIPT"
            try:
                # Bound the response before parsing it. A 2xx alone is not proof of receipt.
                with self.client.stream("POST", "/events", json=payload) as response:
                    body = b""
                    for segment in response.iter_bytes(chunk_size=MAX_BODY + 1):
                        body += segment
                        if len(body) > MAX_BODY:
                            break
                    if (
                        response.status_code == 200
                        and len(body) <= MAX_BODY
                        and json.loads(body) == receipt(event, "ALERT_DELIVERED")
                    ):
                        status, failure = "ALERT_DELIVERED", None
                        break
            except httpx.TimeoutException:
                failure = "TIMEOUT"
            except httpx.TransportError:
                failure = "TRANSPORT_UNAVAILABLE"
            except (ValueError, UnicodeError):
                failure = "INVALID_RECEIPT"
        return {
            "status": status,
            "failure_category": failure,
            "attempts": attempt,
            "attempt_log": deliveries,
            "completed_at": now(),
        }

    def raise_alert(self, code, category):
        event = Alert.create(code, category, self.revision)
        delivery = self.send(event)
        assert delivery["status"] == "ALERT_DELIVERED", delivery
        response = self.client.post(f"/events/{event.correlation}/ack")
        assert response.status_code == 200
        assert response.json() == receipt(event, "ALERT_ACKNOWLEDGED")
        row = {
            "status": "ALERT_CREATED",
            "event": asdict(event),
            "delivery": delivery,
            "acknowledgement": {
                "status": "ALERT_ACKNOWLEDGED",
                "scope": "TEST_ONLY",
                "observed_at": now(),
            },
        }
        self.evidence.append(row)
        return event, row

    def resolve(self, active):
        event, row = active
        resolution = replace(event, observed_at=now(), recovery_status="RESOLVED")
        delivery = self.send(resolution)
        assert delivery["status"] == "ALERT_DELIVERED", delivery
        row["recovery"] = {"event": asdict(resolution), "delivery": delivery}


def receipt(event, status):
    return {
        "status": status,
        "scope": "TEST_ONLY",
        "correlation": event.correlation,
        "recovery_status": event.recovery_status,
        "observed_at": event.observed_at,
    }


@contextmanager
def receiver(revision):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("invalid synthetic revision")
    token = secrets.token_urlsafe(32)
    events = {}
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def answer(self, status, payload):
            body = json.dumps(payload).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                # A timed-out client may leave before the reply. Request details stay private.
                pass

        def do_POST(self):
            supplied = self.headers.get("Authorization", "").encode()
            if not hmac.compare_digest(supplied, f"Bearer {token}".encode()):
                return self.answer(401, {"status": "UNAUTHORIZED"})
            if self.path.endswith("/ack"):
                return self.ack()
            if self.path != "/events":
                return self.answer(404, {"status": "NOT_FOUND"})
            try:
                self.connection.settimeout(0.5)
                length = int(self.headers["Content-Length"])
                if not 0 < length <= MAX_BODY:
                    raise ValueError("invalid payload length")
                event = Alert(**json.loads(self.rfile.read(length)))
                event.validate()
                if event.revision != revision:
                    raise ValueError("wrong revision")
            except (KeyError, ValueError, TypeError, OSError):
                return self.answer(400, {"status": "INVALID_EVENT"})
            with lock:
                prior, acknowledged = events.get(event.correlation, (None, False))
                if prior == event:
                    return self.answer(200, receipt(event, "ALERT_DELIVERED"))
                if prior is None:
                    valid = len(events) < MAX_EVENTS and event.recovery_status == "OPEN"
                else:
                    valid = (
                        acknowledged
                        and prior.recovery_status == "OPEN"
                        and event.recovery_status == "RESOLVED"
                        and event.observed_at >= prior.observed_at
                        and replace(
                            event, observed_at=prior.observed_at, recovery_status="OPEN"
                        )
                        == prior
                    )
                if not valid:
                    return self.answer(409, {"status": "STALE_OR_INVALID_TRANSITION"})
                events[event.correlation] = (event, acknowledged)
            return self.answer(200, receipt(event, "ALERT_DELIVERED"))

        def ack(self):
            correlation = self.path.removeprefix("/events/").removesuffix("/ack")
            with lock:
                event, _ = events.get(correlation, (None, False))
                if event is None or event.recovery_status != "OPEN":
                    return self.answer(404, {"status": "NOT_FOUND"})
                events[correlation] = (event, True)
            return self.answer(200, receipt(event, "ALERT_ACKNOWLEDGED"))

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
        )
        thread.start()
        alerts = SyntheticAlerts(revision, server, token, events, lock)
        try:
            yield alerts
        finally:
            alerts.client.close()
            server.shutdown()
            thread.join(timeout=2)


def security_checks(revision, sentinels):
    import socket
    import time

    def fault(mode, event):
        class Fault(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                if mode == "timeout":
                    time.sleep(0.8)
                    return
                # A valid-looking 200 with the wrong observation must not count.
                body = json.dumps(
                    receipt(
                        replace(event, observed_at="2020-01-01T00:00:00.000+00:00"),
                        "ALERT_DELIVERED",
                    )
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with ThreadingHTTPServer(("127.0.0.1", 0), Fault) as server:
            thread = Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
            )
            thread.start()
            alerts = SyntheticAlerts(
                revision, server, "synthetic-fault-test", {}, Lock()
            )
            try:
                return alerts.send(event)
            finally:
                alerts.client.close()
                server.shutdown()
                thread.join(timeout=2)

    with receiver(revision) as alerts:
        event = Alert.create("backup.integrity", "INTEGRITY_MISMATCH", revision)
        payload = asdict(event)
        unauthorized = httpx.post(
            str(alerts.client.base_url) + "/events",
            json=payload,
            timeout=1,
            trust_env=False,
        )
        assert unauthorized.status_code == 401
        unauth_ack = httpx.post(
            str(alerts.client.base_url) + f"/events/{event.correlation}/ack",
            timeout=1,
            trust_env=False,
        )
        assert unauth_ack.status_code == 401
        for invalid in (
            {**payload, "forbidden": sentinels[0]},
            {**payload, "correlation": sentinels[1]},
        ):
            response = alerts.client.post("/events", json=invalid)
            assert response.status_code == 400 and response.json() == {
                "status": "INVALID_EVENT"
            }
            assert all(secret not in response.text for secret in sentinels)
        assert not alerts.events
        incident = alerts.raise_alert(event.event_code, event.failure_category)
        active, row = incident
        assert alerts.send(active)["status"] == "ALERT_DELIVERED"
        assert len(alerts.events) == 1 and alerts.events[active.correlation] == (
            active,
            True,
        )
        alerts.resolve(incident)
        stale = alerts.client.post("/events", json=asdict(active))
        assert stale.status_code == 409
        assert all(secret not in json.dumps(row) + stale.text for secret in sentinels)
        for mode, expected in (("timeout", "TIMEOUT"), ("invalid", "INVALID_RECEIPT")):
            result = fault(mode, event)
            assert (
                result["status"],
                result["failure_category"],
                result["attempts"],
            ) == ("FAILED", expected, ATTEMPTS)
            row[mode] = result
        url = alerts.client.base_url
    # Release an ephemeral listener to exercise a real refused connection.
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        timeout=0.5,
        trust_env=False,
    ) as client:
        alerts.client = client
        failed = alerts.send(event)
        assert (
            failed["status"],
            failed["failure_category"],
            failed["attempts"],
        ) == ("FAILED", "TRANSPORT_UNAVAILABLE", ATTEMPTS), failed
    row["connection"] = failed
    with httpx.Client(base_url=url, timeout=0.5, trust_env=False) as client:
        try:
            client.get("/")
        except httpx.TransportError:
            pass
        else:
            raise AssertionError("synthetic receiver remained available after cleanup")
    return {
        "duplicated_receipt_idempotent": True,
        "unauthorized_delivery_and_ack": "DENIED",
        "invalid_payload_and_malformed_correlation": "DENIED",
        "stale_delivery": "DENIED",
        "receiver_cleanup": "PASS",
        "transport_failures": {
            key: row[key] for key in ("timeout", "invalid", "connection")
        },
    }
