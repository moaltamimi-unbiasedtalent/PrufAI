"""Separate durable Writing consumer: python -m app.writing_execution.worker."""

from __future__ import annotations

import argparse
import signal
import socket
import sys
import threading
import uuid

from app.config import get_settings
from app.db import create_session_factory
from app.writing_execution.execution import WritingExecutionService


def main() -> None:
    parser = argparse.ArgumentParser(description="Consume durable governed Writing jobs")
    parser.add_argument(
        "--once", action="store_true", help="Claim at most one eligible job and exit"
    )
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args()
    if not 0.1 <= args.poll_interval <= 60:
        parser.error("poll interval must be between 0.1 and 60 seconds")
    settings = get_settings()
    factory = create_session_factory(settings)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop.set())
    signal.signal(signal.SIGINT, lambda *_args: stop.set())
    worker_id = f"{socket.gethostname()[:70]}:{uuid.uuid4()}"
    lease_seconds = max(60, settings.writing_provider_timeout_seconds + 30)
    while not stop.is_set():
        with factory() as session:
            job = WritingExecutionService.claim_next(
                session, worker_id=worker_id, lease_seconds=lease_seconds
            )
            job_id, claim_token = (job.id, job.claim_token) if job is not None else (None, None)
            session.commit()
        if job_id is not None and claim_token is not None:
            result = WritingExecutionService.execute_claim(
                factory, job_id=job_id, claim_token=claim_token, settings=settings
            )
            print(f"Writing job {job_id}: {result.status if result else 'FENCED'}", flush=True)
        if args.once:
            return
        if job_id is None:
            stop.wait(args.poll_interval)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        # Provider/storage/DB exceptions can contain private URLs or credentials.
        print("Writing worker stopped: infrastructure unavailable", file=sys.stderr)
        raise SystemExit(1) from None
