"""Separate durable transcription consumer: python -m app.speech_transcription.worker."""

from __future__ import annotations

import argparse
import signal
import socket
import sys
import threading
import uuid

from app.config import get_settings
from app.db import create_session_factory
from app.knowledge.storage import build_object_storage
from app.speech_transcription.execution import TranscriptionExecutionService


def main() -> None:
    parser = argparse.ArgumentParser(description="Consume durable governed transcription jobs")
    parser.add_argument(
        "--once", action="store_true", help="Claim at most one eligible job and exit"
    )
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args()
    if not 0.1 <= args.poll_interval <= 60:
        parser.error("poll interval must be between 0.1 and 60 seconds")
    settings = get_settings()
    if settings.object_storage_backend != "s3":
        parser.error("transcription worker requires private S3-compatible object storage")
    factory = create_session_factory(settings)
    storage = build_object_storage(settings)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    worker_id = f"{socket.gethostname()[:70]}:{uuid.uuid4()}"
    lease_seconds = max(60, settings.speech_transcription_provider_timeout_seconds + 30)
    while not stop.is_set():
        with factory() as session:
            job = TranscriptionExecutionService.claim_next(
                session, worker_id=worker_id, lease_seconds=lease_seconds
            )
            job_id, claim_token = (job.id, job.claim_token) if job is not None else (None, None)
            session.commit()
        if job_id is not None and claim_token is not None:
            result = TranscriptionExecutionService.execute_claim(
                factory, job_id=job_id, claim_token=claim_token, storage=storage, settings=settings
            )
            print(
                f"transcription job {job_id}: {result.status if result else 'FENCED'}", flush=True
            )
        if args.once:
            return
        if job_id is None:
            stop.wait(args.poll_interval)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        # Provider/storage/DB exceptions can contain private URLs or credentials.
        print("transcription worker stopped: infrastructure unavailable", file=sys.stderr)
        raise SystemExit(1) from None
