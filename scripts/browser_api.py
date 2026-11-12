"""Isolated browser-test server with explicit, negative-only recovery faults.

This is never imported by the product API. Successful requests still use the
normal authentication, services, PostgreSQL and private S3 adapter unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import threading
import uuid
from contextvars import ContextVar
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

FAULT_PATH = re.compile(
    r"^/api/v1/candidate/audio-recordings/([0-9a-f-]{36})/(finalize|recover)$"
)
START_PATH = re.compile(
    r"^/api/v1/candidate/attempts/([0-9a-f-]{36})/items/[0-9a-f-]{36}/audio-recordings$"
)
fault_context: ContextVar[tuple[str, uuid.UUID] | None] = ContextVar(
    "audio_test_fault", default=None
)
claimed: set[tuple[str, uuid.UUID]] = set()
claim_lock = threading.Lock()


def claim(kind: str) -> bool:
    operation = fault_context.get()
    if operation is None or operation[0] != kind:
        return False
    with claim_lock:
        if operation in claimed:
            return False
        claimed.add(operation)
        return True


def main() -> None:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--port", type=int, required=True)
    port = arguments.parse_args().port
    database = urlparse(os.environ.get("DATABASE_URL", ""))
    endpoint = urlparse(os.environ.get("OBJECT_STORAGE_ENDPOINT", ""))
    if not (
        os.environ.get("PRUFAI_E2E_AUDIO_FAILURES") == "1"
        and os.environ.get("PRUFAI_E2E_ALLOW_DEMO") == "1"
        and os.environ.get("PRUFAI_DEMO_ENABLED") == "1"
        and os.environ.get("ENVIRONMENT") == "development"
        and os.environ.get("OBJECT_STORAGE_BACKEND") == "s3"
        and database.scheme == "postgresql+psycopg"
        and database.hostname in {"localhost", "127.0.0.1"}
        and "browser" in database.path
        and endpoint.hostname in {"localhost", "127.0.0.1"}
        and 1024 <= port <= 65535
    ):
        raise SystemExit(
            "Recovery-fault launcher requires an explicit isolated local browser setup."
        )

    import uvicorn
    from app.auth.delivery import EnrollmentMessage, enrollment_delivery
    from app.evaluation.disposable import assert_disposable_database
    from app.knowledge.storage import S3CompatibleObjectStorage, StorageError
    from app.main import app
    from app.models import CandidateAudioRecordingSession, CandidateMediaAsset
    from app.models.enums import CandidateMediaStatus
    from app.speaking_audio.service import SpeakingAudioService
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session

    capture_name = os.environ.get("PRUFAI_E2E_ENROLLMENT_CAPTURE")
    if capture_name:
        assert_disposable_database(os.environ["DATABASE_URL"])
        capture = Path(capture_name)
        if (
            os.environ.get("AUTH_ENROLLMENT_DELIVERY_MODE") != "TEST_ONLY"
            or not capture.is_absolute()
            or capture.name != "messages.jsonl"
            or capture.parent.parent.resolve() != Path("/tmp").resolve()
            or not capture.parent.name.startswith("prufai-browser-enrollment.")
            or capture.parent.is_symlink()
            or capture.parent.stat().st_uid != os.getuid()
            or stat.S_IMODE(capture.parent.stat().st_mode) != 0o700
        ):
            raise SystemExit("Enrollment capture requires the private disposable runner directory.")

        class Capture:
            def send(self, message: EnrollmentMessage) -> None:
                if not message.recipient.endswith(".test"):
                    raise OSError("Synthetic enrollment recipients must use the .test domain.")
                data = json.dumps({
                    "purpose": message.purpose,
                    "recipient": message.recipient,
                    "token": message.token,
                    "organisation_id": str(message.organisation_id) if message.organisation_id else None,
                }).encode() + b"\n"
                descriptor = os.open(
                    capture, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(descriptor, "ab", buffering=0) as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                        raise OSError("Enrollment capture file is not private.")
                    handle.write(data)

        adapter = Capture()
        app.dependency_overrides[enrollment_delivery] = lambda: adapter

    original_flush = Session.flush
    original_exists = S3CompatibleObjectStorage.exists
    original_start = SpeakingAudioService.start_session

    def flush(session: Session, objects: Any = None) -> None:
        operation = fault_context.get()
        # READY construction happens only after the real PUT and actual
        # object/checksum verification. Fail the database receipt once.
        if (
            operation
            and any(
                isinstance(row, CandidateMediaAsset)
                and row.recording_session_id == operation[1]
                for row in session.new
            )
            and claim("receipt-after-put-once")
        ):
            raise SQLAlchemyError("Controlled browser-test database receipt failure")
        original_flush(session, objects)

    def exists(storage: S3CompatibleObjectStorage, key: str) -> bool:
        if claim("recover-storage-once"):
            raise StorageError("Controlled browser-test storage readiness failure")
        return original_exists(storage, key)

    def start_session(*args: Any, **kwargs: Any) -> CandidateAudioRecordingSession:
        recording = original_start(*args, **kwargs)
        operation = fault_context.get()
        if operation and operation[0] == "recording-expiry-soon":
            token = fault_context.set((operation[0], recording.id))
            try:
                if (
                    recording.status == CandidateMediaStatus.RECORDING
                    and recording.recording_expires_at is not None
                    and claim("recording-expiry-soon")
                ):
                    recording.recording_expires_at = min(
                        recording.recording_expires_at,
                        datetime.now(timezone.utc) + timedelta(milliseconds=1500),
                    )
            finally:
                fault_context.reset(token)
        return recording

    Session.flush = flush  # type: ignore[method-assign]
    S3CompatibleObjectStorage.exists = exists  # type: ignore[method-assign]
    SpeakingAudioService.start_session = staticmethod(start_session)

    @app.middleware("http")
    async def request_fault(request: Request, call_next: Any) -> Any:
        requested = request.headers.get("x-prufai-test-fault")
        if not requested:
            return await call_next(request)
        match = FAULT_PATH.fullmatch(request.url.path)
        start = START_PATH.fullmatch(request.url.path)
        expected = {
            "finalize": "receipt-after-put-once",
            "recover": "recover-storage-once",
        }
        recording_fault = match is not None and requested == expected[match[2]]
        start_fault = start is not None and requested == "recording-expiry-soon"
        if request.method != "POST" or not (recording_fault or start_fault):
            return JSONResponse(
                status_code=400, content={"detail": "Invalid test fault scope"}
            )
        identity = match[1] if match else start[1]  # type: ignore[index]
        token = fault_context.set((requested, uuid.UUID(identity)))
        try:
            return await call_next(request)
        finally:
            fault_context.reset(token)

    print(
        "Isolated browser recovery faults enabled; normal success paths use real services."
    )
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
